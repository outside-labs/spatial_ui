// Bounded single-record event connections. No writes or component callbacks.
const encoder = new TextEncoder();
const cursorPattern = /^[0-9a-f]{32}$/;
const plain = value => value !== null && typeof value === "object" && !Array.isArray(value);
const exact = (value, names) => plain(value) && Object.keys(value).length === names.length
  && names.every(name => Object.hasOwn(value, name));

export function strictJSON(text, maximum = 65536) {
  if (typeof text !== "string" || encoder.encode(text).length > maximum) throw new Error("Invalid event size");
  let offset = 0;
  const whitespace = () => { while (/[\x20\t\r\n]/.test(text[offset] || "x")) offset++; };
  function string() {
    const start = offset++;
    while (offset < text.length) {
      const character = text[offset++];
      if (character === "\\") { offset++; continue; }
      if (character === '"') {
        const value = JSON.parse(text.slice(start, offset));
        if (!value.isWellFormed()) throw new Error("Invalid Unicode");
        return value;
      }
    }
    throw new Error("Invalid JSON string");
  }
  function read(depth) {
    if (depth > 32) throw new Error("Event nesting exceeds its limit");
    whitespace();
    const character = text[offset];
    if (character === '"') return string();
    if (character === "{" || character === "[") {
      offset++;
      const object = character === "{";
      const result = object ? Object.create(null) : [];
      const end = object ? "}" : "]";
      whitespace();
      if (text[offset] === end) { offset++; return result; }
      while (true) {
        whitespace();
        if (object) {
          if (text[offset] !== '"') throw new Error("Invalid JSON key");
          const name = string();
          if (Object.hasOwn(result, name)) throw new Error("Duplicate event field");
          whitespace();
          if (text[offset++] !== ":") throw new Error("Invalid JSON object");
          result[name] = read(depth + 1);
        } else result.push(read(depth + 1));
        whitespace();
        const separator = text[offset++];
        if (separator === end) return result;
        if (separator !== ",") throw new Error("Invalid JSON separator");
      }
    }
    for (const [token, value] of [["true", true], ["false", false], ["null", null]]) {
      if (text.startsWith(token, offset)) { offset += token.length; return value; }
    }
    const number = /^-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?/.exec(text.slice(offset));
    if (!number || !Number.isFinite(Number(number[0]))) throw new Error("Invalid JSON value");
    offset += number[0].length;
    return Number(number[0]);
  }
  const value = read(0);
  whitespace();
  if (offset !== text.length) throw new Error("Trailing JSON content");
  return value;
}

export function eventMessage(text, config) {
  const message = strictJSON(text, config.maxMessageBytes);
  if (exact(message, ["v", "type"]) && message.v === 1 && message.type === "resync") return { type: "resync" };
  if (!exact(message, ["v", "type", "cursor", "value"]) || message.v !== 1 || message.type !== "snapshot"
      || typeof message.cursor !== "string" || !cursorPattern.test(message.cursor) || !plain(message.value)) {
    throw new Error("Unsupported resource event");
  }
  const value = Object.create(null);
  for (const [name, kind] of Object.entries(config.fields)) {
    const item = message.value[name];
    if (!Object.hasOwn(message.value, name) || (kind === "boolean" ? typeof item !== "boolean"
      : typeof item !== "string" || encoder.encode(item).length > 4096)) throw new Error("Invalid visible field");
    value[name] = item;
  }
  if (value[config.keyField] !== config.key) throw new Error("Resource identity changed");
  return { type: "snapshot", cursor: message.cursor, value };
}

export function configuration(value) {
  if (!exact(value, ["v", "path", "key", "keyField", "fields", "baseline", "maxMessageBytes",
      "retryLimit", "retryDelayMs", "handshakeTimeoutMs"]) || value.v !== 1
      || typeof value.path !== "string" || value.path.length > 641
      || !/^\/[A-Za-z0-9_-]+(?:\/[A-Za-z0-9_-]+)*\/[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/.test(value.path)
      || typeof value.key !== "string" || !/^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/.test(value.key)
      || !value.path.endsWith("/" + value.key) || !plain(value.fields)
      || Object.keys(value.fields).length < 1 || Object.keys(value.fields).length > 64
      || typeof value.keyField !== "string" || value.fields[value.keyField] !== "text") throw new Error("Invalid live configuration");
  for (const [name, kind] of Object.entries(value.fields)) {
    if (!/^[A-Za-z][A-Za-z0-9_-]{0,63}$/.test(name) || !["text", "boolean"].includes(kind)) throw new Error("Invalid visible field");
  }
  for (const [name, minimum, maximum] of [["maxMessageBytes", 1024, 65536], ["retryLimit", 0, 8],
    ["retryDelayMs", 100, 5000], ["handshakeTimeoutMs", 100, 30000]]) {
    if (!Number.isInteger(value[name]) || value[name] < minimum || value[name] > maximum) throw new Error("Invalid connection bound");
  }
  eventMessage(JSON.stringify({ v: 1, type: "snapshot", cursor: "0".repeat(32), value: value.baseline }), value);
  return value;
}

export class Subscription {
  constructor(config, notify, { origin = globalThis.location.origin, Socket = globalThis.WebSocket,
    schedule = setTimeout, cancel = clearTimeout, now = () => performance.now() } = {}) {
    this.config = configuration(config);
    const base = new URL(origin);
    if (base.origin !== origin || !["http:", "https:"].includes(base.protocol)) throw new Error("Invalid application origin");
    base.protocol = base.protocol === "https:" ? "wss:" : "ws:";
    base.pathname = config.path;
    this.url = base.href;
    this.notify = notify;
    this.Socket = Socket;
    this.schedule = schedule;
    this.cancel = cancel;
    this.now = now;
    this.generation = this.attempts = 0;
    this.socket = this.timer = this.cursor = this.value = null;
    this.closed = false;
    this.connect();
  }

  report(status) { this.notify({ status, value: this.value, cursor: this.cursor }); }

  retire() {
    this.generation++;
    if (this.timer !== null) this.cancel(this.timer);
    this.timer = null;
    const socket = this.socket;
    this.socket = null;
    if (socket) {
      socket.onopen = socket.onmessage = socket.onclose = socket.onerror = null;
      socket.close();
    }
  }

  connect() {
    if (this.closed) return;
    this.retire();
    const generation = this.generation;
    const current = () => !this.closed && this.generation === generation;
    this.report(this.attempts ? "reconnecting" : "connecting");
    let socket;
    try { socket = this.socket = new this.Socket(this.url, "expose-api.events.v1"); }
    catch { this.retry(); return; }
    let firstSnapshot = null;
    this.timer = this.schedule(() => { if (current()) this.retry(); }, this.config.handshakeTimeoutMs);
    socket.onopen = () => {
      if (!current()) return;
      if (socket.protocol !== "expose-api.events.v1") { this.terminal(); return; }
      try { socket.send(JSON.stringify({ v: 1, type: "subscribe", cursor: this.cursor })); }
      catch { this.retry(); }
    };
    socket.onmessage = event => {
      if (!current()) return;
      try {
        const message = eventMessage(event.data, this.config);
        if (message.type === "resync") {
          this.value = this.cursor = null;
          // A resync must be followed by a fresh snapshot within a finite bound.
          if (this.timer !== null) this.cancel(this.timer);
          this.timer = this.schedule(() => { if (current()) this.retry(); }, this.config.handshakeTimeoutMs);
          this.report("resyncing");
          return;
        }
        if (this.timer !== null) this.cancel(this.timer);
        this.timer = null;
        if (firstSnapshot === null) firstSnapshot = this.now();
        if (this.now() - firstSnapshot >= 30000) this.attempts = 0;
        if (message.cursor === this.cursor) { this.report("live"); return; }
        this.cursor = message.cursor;
        this.value = message.value;
        this.report("live");
      } catch { this.terminal(); }
    };
    socket.onclose = event => {
      if (!current()) return;
      if (firstSnapshot !== null && this.now() - firstSnapshot >= 30000) this.attempts = 0;
      if ([1000, 1002, 1003, 1007, 1008, 1009].includes(event.code)) this.terminal();
      else this.retry();
    };
    // Browsers deliver a close after connection errors, including handshake denial.
    socket.onerror = () => {};
  }

  retry() {
    if (this.closed) return;
    this.retire();
    if (this.attempts >= this.config.retryLimit) { this.terminal(); return; }
    const delay = Math.min(10000, this.config.retryDelayMs * 2 ** this.attempts++);
    const generation = this.generation;
    this.report("reconnecting");
    this.timer = this.schedule(() => {
      this.timer = null;
      if (!this.closed && generation === this.generation) this.connect();
    }, delay);
  }

  terminal() {
    this.retire();
    this.closed = true;
    this.value = this.cursor = null;
    this.report("unavailable");
  }

  stop() {
    this.retire();
    this.closed = true;
    this.value = this.cursor = null;
  }
}

const messages = {
  connecting: "Connecting to live updates. Your edits are preserved.",
  reconnecting: "Connection interrupted. Your edits are preserved. Reconnecting…",
  resyncing: "Refreshing live updates. Your edits are preserved.",
  unavailable: "Live updates unavailable. Your edits are preserved. Reload to reconnect.",
};

export class LiveConnections {
  constructor(root, options = {}) {
    this.root = root;
    this.options = options;
    this.entries = new Map();
    this.closed = false;
  }

  render(entry) {
    const { node, config, state } = entry;
    const status = node.querySelector("[data-live-status]");
    const values = node.querySelector("[data-live-value]");
    if (!status || !values) return;
    node.dataset.suiLiveState = state.status;
    const changed = state.value && Object.keys(config.fields).some(name => state.value[name] !== config.baseline[name]);
    status.textContent = state.status === "live" ? changed
      ? "Remote update available. Load / refresh before merging your edits."
      : "Live updates connected." : messages[state.status];
    const children = [];
    if (state.value) for (const name of Object.keys(config.fields)) {
      const term = node.ownerDocument.createElement("dt");
      const description = node.ownerDocument.createElement("dd");
      term.textContent = name;
      description.textContent = String(state.value[name]);
      children.push(term, description);
    }
    values.replaceChildren(...children);
  }

  scan() {
    if (this.closed) return;
    const nodes = new Set(this.root.querySelectorAll("[data-sui-live]"));
    for (const [node, entry] of this.entries) {
      if (!nodes.has(node)) { entry.connection.stop(); this.entries.delete(node); }
    }
    for (const node of nodes) {
      let entry = this.entries.get(node);
      try {
        const config = configuration(strictJSON(node.dataset.suiLive));
        const signature = JSON.stringify({ ...config, baseline: null });
        if (entry && entry.signature !== signature) {
          entry.connection.stop(); this.entries.delete(node); entry = null;
        }
        if (!entry) {
          if (this.entries.size >= 16) throw new Error("Live editor capacity reached");
          entry = { node, config, signature, state: { status: "connecting", value: null } };
          this.entries.set(node, entry);
          entry.connection = new Subscription(config, state => { entry.state = state; this.render(entry); }, this.options);
        } else entry.config = config;
        this.render(entry);
      } catch {
        entry?.connection?.stop();
        this.entries.delete(node);
        const status = node.querySelector("[data-live-status]");
        if (status) status.textContent = messages.unavailable;
        node.querySelector("[data-live-value]")?.replaceChildren();
        node.dataset.suiLiveState = "unavailable";
      }
    }
  }

  stop() {
    this.closed = true;
    for (const entry of this.entries.values()) entry.connection.stop();
    this.entries.clear();
  }
}
