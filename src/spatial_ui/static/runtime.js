import { LiveConnections } from "./live.js";

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
  let stopped = false;
  let queued = 0;
  let request = null;
  const listeners = new AbortController();
  const forms = new WeakMap();
  const compositions = new WeakMap();
  const tablePoll = setInterval(() => {
    if (stopped || failed || queued || compositions.get(document.activeElement) === true) return;
    app.querySelector("button[data-table-poll]")?.click();
  }, 150);
  const live = new LiveConnections(app);
  live.scan();
  const stateOf = form => {
    if (!forms.has(form)) forms.set(form, { sequence: 0, dirty: new Map(), pending: false });
    return forms.get(form);
  };

  function tableTextValid(control) {
    if (!control.matches("input[data-table-edit-input]")) return true;
    const valid = new TextEncoder().encode(control.value).length <= 4096;
    control.setCustomValidity(valid ? "" : "Cell text exceeds 4,096 UTF-8 bytes. Shorten it before saving.");
    if (!valid) {
      control.setAttribute("aria-invalid", "true");
      control.dataset.tableByteInvalid = "true";
    } else if (control.hasAttribute("data-table-byte-invalid")) {
      control.removeAttribute("data-table-byte-invalid");
      control.removeAttribute("aria-invalid");
    }
    return valid;
  }

  function checkTableText(root = app) {
    let valid = true;
    for (const control of root.querySelectorAll("input[data-table-edit-input]")) {
      if (tableTextValid(control)) continue;
      valid = false;
      status.textContent = "Cell text exceeds 4,096 UTF-8 bytes. Shorten it or discard the draft.";
    }
    return valid;
  }

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

  reducedMotion.addEventListener("change", () => { cancelMotion(); resting = measure(); }, { signal: listeners.signal });

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
    const form = editable ? current.closest("form[data-sui-form]") : null;
    const preserve = editable && (!form || stateOf(form).dirty.has(current.name) || compositions.has(current));
    const value = editable ? current.value : null;
    const checked = current.matches("input") ? current.checked : null;
    const selected = current.matches("select") ? new Set([...current.selectedOptions].map(option => option.value)) : null;
    for (const attribute of [...current.attributes]) {
      if (!next.hasAttribute(attribute.name)) current.removeAttribute(attribute.name);
    }
    for (const attribute of next.attributes) {
      if (current.getAttribute(attribute.name) !== attribute.value) current.setAttribute(attribute.name, attribute.value);
    }
    if (!(editable && compositions.get(current) === true)) patchChildren(current, next);
    if (preserve && !current.matches('input[type="file"]') && current.value !== value) current.value = value;
    if (preserve && checked !== null) current.checked = checked;
    if (preserve && selected) for (const option of current.options) option.selected = selected.has(option.value);
    if (editable && !preserve) {
      if (current.matches("input")) { current.value = next.value; current.checked = next.checked; }
      if (current.matches("textarea, select")) current.value = next.value;
    }
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

  function enqueue(element, payload = null, state = null) {
    if (stopped || failed) return;
    if (queued >= 64) { stop(); return; }
    queued += 1;
    const sequence = state?.sequence;
    queue = queue.then(async () => {
      if (stopped || failed || !element.isConnected) return;
      app.setAttribute("aria-busy", "true");
      try {
        const envelope = { event: payload ? element.dataset.suiForm : element.dataset.suiClick,
                           version: Number(app.dataset.version) };
        if (payload) envelope.payload = payload;
        const body = JSON.stringify(envelope);
        if (new TextEncoder().encode(body).length > 16384) throw new Error("Event exceeds its size limit");
        request = new AbortController();
        const response = await fetch("/_spatial/event", {
          method: "POST", credentials: "same-origin",
          headers: { "Content-Type": "application/json", "X-Spatial-Token": csrf },
          body, signal: request.signal
        });
        const result = await response.json();
        if (stopped) return;
        if (!response.ok) throw new Error("Update failed");
        if (!Number.isSafeInteger(result.version) || result.version <= Number(app.dataset.version)
            || typeof result.markup !== "string" || typeof result.css !== "string") throw new Error("Invalid response");
        const currentEdit = state && state.sequence === sequence;
        if (currentEdit && payload?.kind === "submit" && ["saved", "loaded", "deleted"].includes(result.form?.status)) {
          state.dirty.clear();
        }
        const before = measure();
        cancelMotion();
        const active = document.activeElement;
        const previousEditor = app.querySelector("form[data-table-editor]");
        const selection = active && typeof active.selectionStart === "number"
          ? [active.selectionStart, active.selectionEnd, active.selectionDirection] : null;
        const template = document.createElement("template");
        template.innerHTML = result.markup;
        stylesheet.textContent = result.css;
        patchChildren(app, template.content);
        app.dataset.version = String(result.version);
        if (active?.isConnected && document.activeElement !== active) active.focus({ preventScroll: true });
        if (!active?.isConnected) {
          const editor = app.querySelector("form[data-table-editor]");
          if (editor && editor !== previousEditor) editor.elements.namedItem("value")?.focus({ preventScroll: true });
          else if (previousEditor && !editor) app.querySelector('td[aria-selected="true"] button[data-table-cell]')?.focus({ preventScroll: true });
        }
        if (selection && active?.isConnected) active.setSelectionRange(...selection);
        transition(before);
        observe();
        live.scan();
        status.textContent = result.form?.message || "";
        if (result.form && state && currentEdit) {
          for (const control of element.elements) control.removeAttribute("aria-invalid");
          for (const error of result.form.errors) {
            const control = element.elements.namedItem(error.name);
            if (control) control.setAttribute("aria-invalid", "true");
          }
          if (payload.kind === "submit" && result.form.focus) {
            element.elements.namedItem(result.form.focus)?.focus({ preventScroll: true });
          }
        }
        checkTableText();
      } finally {
        request = null;
        if (state && payload.kind === "submit") { state.pending = false; element.removeAttribute("aria-busy"); }
        app.removeAttribute("aria-busy");
      }
    }).catch(() => {
      failed = true;
      status.textContent = "The request outcome is unknown. Your edits are preserved. Reload to reconcile it.";
    }).finally(() => { queued -= 1; });
  }

  app.addEventListener("click", event => {
    const button = event.target.closest?.("button[data-sui-click]");
    if (!button || !app.contains(button) || button.disabled) return;
    event.preventDefault();
    enqueue(button);
  }, { signal: listeners.signal });

  app.addEventListener("keydown", event => {
    const editor = event.target.closest?.("form[data-table-editor]");
    if (editor && !event.isComposing && compositions.get(event.target) !== true) {
      if (event.key === "Escape") {
        event.preventDefault();
        editor.querySelector("button[data-table-discard]")?.click();
        return;
      }
      if (event.key === "Enter" && event.target.matches("input, select")) {
        event.preventDefault();
        editor.requestSubmit();
        return;
      }
    }
    const cell = event.target.closest?.("button[data-table-cell]");
    if (!cell || event.isComposing || event.altKey || event.metaKey || event.shiftKey) return;
    const table = cell.closest("table");
    const rows = [...table.tBodies[0].rows];
    let row = rows.indexOf(cell.closest("tr"));
    let column = cell.closest("td").cellIndex - 1;
    const width = rows[row].querySelectorAll("button[data-table-cell]").length;
    if (event.key === "ArrowDown") row += 1;
    else if (event.key === "ArrowUp") row -= 1;
    else if (event.key === "ArrowRight") column += 1;
    else if (event.key === "ArrowLeft") column -= 1;
    else if (event.key === "Home") { column = 0; if (event.ctrlKey) row = 0; }
    else if (event.key === "End") { column = width - 1; if (event.ctrlKey) row = rows.length - 1; }
    else return;
    event.preventDefault();
    const target = rows[Math.max(0, Math.min(rows.length - 1, row))]
      .querySelectorAll("button[data-table-cell]")[Math.max(0, Math.min(width - 1, column))];
    target.focus({ preventScroll: true });
    target.scrollIntoView({ block: "nearest", inline: "nearest", behavior: "instant" });
    target.click();
  }, { signal: listeners.signal });

  const fieldValue = control => control.type === "checkbox" ? control.checked : control.value;
  function edit(event) {
    const control = event.target;
    const form = control.closest?.("form[data-sui-form]");
    if (!form || !control.name || control.disabled || event.isComposing || compositions.get(control) === true) return;
    const value = fieldValue(control);
    if (event.type === "input" && compositions.get(control) === value) { compositions.delete(control); return; }
    const state = stateOf(form);
    state.sequence += 1;
    state.dirty.set(control.name, value);
    if (!tableTextValid(control)) { checkTableText(); return; }
    enqueue(form, { kind: event.type, fields: { [control.name]: value } }, state);
  }
  for (const kind of ["input", "change"]) app.addEventListener(kind, edit, { signal: listeners.signal });
  app.addEventListener("compositionstart", event => { compositions.set(event.target, true); }, { signal: listeners.signal });
  app.addEventListener("compositionend", event => {
    compositions.delete(event.target);
    edit({ target: event.target, type: "input", isComposing: false });
    compositions.set(event.target, fieldValue(event.target));
  }, { signal: listeners.signal });
  app.addEventListener("submit", event => {
    const form = event.target;
    if (!form.matches("form[data-sui-form]")) return;
    event.preventDefault();
    if (!checkTableText(form)) return;
    const state = stateOf(form);
    if (state.pending || [...form.elements].some(control => compositions.get(control) === true)) return;
    const fields = {};
    for (const control of form.elements) {
      if (control.matches("input, textarea, select") && control.name && !control.matches(":disabled")) fields[control.name] = fieldValue(control);
    }
    state.pending = true;
    form.setAttribute("aria-busy", "true");
    enqueue(form, { kind: "submit", fields }, state);
  }, { signal: listeners.signal });
  app.addEventListener("reset", event => {
    const form = event.target;
    if (!form.matches("form[data-sui-form]")) return;
    const state = stateOf(form);
    state.sequence += 1;
    state.dirty.clear();
    queueMicrotask(() => {
      if (!stopped) for (const control of form.elements) {
        if (control.matches("input, textarea, select") && control.name && !control.matches(":disabled")) {
          edit({ target: control, type: "change", isComposing: false });
        }
      }
    });
  }, { signal: listeners.signal });

  function stop() {
    if (stopped) return;
    stopped = true;
    request?.abort();
    clearInterval(tablePoll);
    if (app.querySelector("[data-table-controls]")) {
      fetch("/_spatial/close", { method: "POST", credentials: "same-origin", keepalive: true,
        headers: { "Content-Type": "application/json", "X-Spatial-Token": csrf }, body: "{}" }).catch(() => {});
    }
    live.stop();
    listeners.abort();
    observer.disconnect();
    observed.clear();
    cancelMotion();
    app.removeAttribute("aria-busy");
    app.inert = true;
    for (const form of app.querySelectorAll("form[data-sui-form]")) form.removeAttribute("aria-busy");
    status.textContent = "Stopped. A dispatched save may have committed. Reload to reconcile it.";
  }
  app.addEventListener("sui:stop", stop, { signal: listeners.signal });
  window.addEventListener("pagehide", stop, { signal: listeners.signal });
  window.addEventListener("pageshow", event => { if (event.persisted) location.reload(); }, { signal: listeners.signal });
})();
