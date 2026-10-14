(() => {
  "use strict";
  const app = document.getElementById("sui-app");
  const stylesheet = document.getElementById("sui-layout");
  const status = document.getElementById("sui-status");
  const csrf = document.querySelector('meta[name="sui-token"]').content;
  const reducedMotion = matchMedia("(prefers-reduced-motion: reduce)");
  const animations = new Map();
  const spatialNodes = () => [...app.querySelectorAll("[data-sui-grid] > [data-sui-key]")];
  const measure = () => new Map(spatialNodes().map(node => [node, node.getBoundingClientRect()]));
  let resting = measure();
  let queue = Promise.resolve();
  let failed = false;

  function cancelMotion() {
    for (const animation of animations.values()) animation.cancel();
    animations.clear();
  }

  function transition(before) {
    cancelMotion();
    const after = measure();
    resting = after;
    if (reducedMotion.matches) return;
    const moving = new Set();
    for (const [node, last] of after) {
      const first = before.get(node);
      const settings = node.closest("[data-sui-motion]");
      const duration = Number(settings?.dataset.suiMotion || 0);
      if (!first || !duration || !last.width || !last.height || typeof node.animate !== "function") continue;
      let parent = node.parentElement;
      while (parent && !moving.has(parent)) parent = parent.parentElement;
      if (parent) continue;
      const x = first.left - last.left;
      const y = first.top - last.top;
      const sx = first.width / last.width;
      const sy = first.height / last.height;
      if (Math.abs(x) + Math.abs(y) + Math.abs(sx - 1) + Math.abs(sy - 1) < 0.02) continue;
      const animation = node.animate([
        { transform: `translate(${x}px, ${y}px) scale(${sx}, ${sy})`, transformOrigin: "top left" },
        { transform: "none", transformOrigin: "top left" }
      ], { duration, easing: settings.dataset.suiEasing || "ease-out" });
      animations.set(node, animation);
      moving.add(node);
      animation.finished.then(() => {
        if (animations.get(node) === animation) animations.delete(node);
      }).catch(() => {});
    }
  }

  reducedMotion.addEventListener("change", () => { cancelMotion(); resting = measure(); });

  function nodeKey(node) {
    return node.nodeType === Node.ELEMENT_NODE
      ? (node.getAttribute("data-sui-key") || node.id || null) : null;
  }

  function compatible(current, next) {
    return current.nodeType === next.nodeType && current.nodeName === next.nodeName
      && nodeKey(current) === nodeKey(next);
  }

  function patch(current, next) {
    if (!compatible(current, next)) {
      const replacement = next.cloneNode(true);
      current.replaceWith(replacement);
      return replacement;
    }
    if (current.nodeType === Node.TEXT_NODE) {
      if (current.data !== next.data) current.data = next.data;
      return current;
    }
    if (current.nodeType !== Node.ELEMENT_NODE) return current;
    const editable = current.matches("input, textarea, select");
    const value = editable ? current.value : null;
    const checked = current.matches("input") ? current.checked : null;
    const selected = current.matches("select") ? new Set([...current.selectedOptions].map(option => option.value)) : null;
    for (const attribute of [...current.attributes]) {
      if (!next.hasAttribute(attribute.name)) current.removeAttribute(attribute.name);
    }
    for (const attribute of next.attributes) {
      if (current.getAttribute(attribute.name) !== attribute.value) current.setAttribute(attribute.name, attribute.value);
    }
    patchChildren(current, next);
    if (editable && !current.matches('input[type="file"]')) current.value = value;
    if (checked !== null) current.checked = checked;
    if (selected) for (const option of current.options) option.selected = selected.has(option.value);
    return current;
  }

  function patchChildren(current, next) {
    const old = [...current.childNodes];
    const keyed = new Map(old.filter(node => nodeKey(node)).map(node => [nodeKey(node), node]));
    const unkeyed = old.filter(node => !nodeKey(node));
    const retained = new Set();
    let cursor = current.firstChild;
    for (const child of next.childNodes) {
      const key = nodeKey(child);
      let existing = key ? keyed.get(key) : unkeyed.shift();
      if (existing) {
        if (existing !== cursor) current.insertBefore(existing, cursor);
        existing = patch(existing, child);
      } else {
        existing = child.cloneNode(true);
        current.insertBefore(existing, cursor);
      }
      retained.add(existing);
      cursor = existing.nextSibling;
    }
    for (const child of [...current.childNodes]) if (!retained.has(child)) child.remove();
  }

  const observed = new Map();
  const observer = new ResizeObserver(entries => {
    let resized = false;
    for (const entry of entries) {
      const last = observed.get(entry.target);
      const size = [entry.target.clientWidth, entry.target.clientHeight];
      if (last && (Math.abs(size[0] - last[0]) > 1 || Math.abs(size[1] - last[1]) > 1)) resized = true;
      observed.set(entry.target, size);
    }
    if (resized) transition(resting);
  });
  function observe() {
    const viewports = new Set(app.querySelectorAll("[data-sui-viewport]"));
    for (const viewport of observed.keys()) {
      if (!viewports.has(viewport)) { observer.unobserve(viewport); observed.delete(viewport); }
    }
    for (const viewport of viewports) {
      if (!observed.has(viewport)) observer.observe(viewport);
      observed.set(viewport, [viewport.clientWidth, viewport.clientHeight]);
    }
  }
  observe();

  app.addEventListener("click", event => {
    const button = event.target.closest?.("button[data-sui-click]");
    if (!button || !app.contains(button) || button.disabled) return;
    event.preventDefault();
    queue = queue.then(async () => {
      if (failed || !button.isConnected || button.disabled) return;
      app.setAttribute("aria-busy", "true");
      try {
        const response = await fetch("/_spacial/event", {
          method: "POST", credentials: "same-origin",
          headers: { "Content-Type": "application/json", "X-Spacial-Token": csrf },
          body: JSON.stringify({ event: button.dataset.suiClick, version: Number(app.dataset.version) })
        });
        const result = await response.json();
        if (!response.ok) throw new Error(result.error || "Update failed; refresh the page");
        const before = measure();
        cancelMotion();
        const active = document.activeElement;
        const selection = active && typeof active.selectionStart === "number"
          ? [active.selectionStart, active.selectionEnd, active.selectionDirection] : null;
        const template = document.createElement("template");
        template.innerHTML = result.markup;
        stylesheet.textContent = result.css;
        patchChildren(app, template.content);
        app.dataset.version = String(result.version);
        if (active?.isConnected && document.activeElement !== active) active.focus({ preventScroll: true });
        if (selection && active?.isConnected) active.setSelectionRange(...selection);
        transition(before);
        observe();
        status.textContent = "";
      } finally {
        app.removeAttribute("aria-busy");
      }
    }).catch(error => {
      failed = true;
      status.textContent = `${error.message}. Refresh this page to continue.`;
    });
  });
})();
