import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const asset = process.argv[2] || new URL("../src/spatial_ui/static/live.js", import.meta.url);
const source = await readFile(asset);
const { Subscription, configuration, eventMessage, strictJSON } = await import("data:text/javascript;base64," + source.toString("base64"));
const config = () => ({ v: 1, path: "/api/notes/note-1", key: "note-1", keyField: "key",
  fields: { key: "text", title: "text", done: "boolean" }, baseline: { key: "note-1", title: "Old", done: false },
  maxMessageBytes: 65536, retryLimit: 2, retryDelayMs: 100, handshakeTimeoutMs: 500 });
const snapshot = (cursor = "1".repeat(32), title = "Current") => JSON.stringify({ v: 1, type: "snapshot", cursor,
  value: { key: "note-1", title, done: false, secret: "hidden" } });

function fixture() {
  const sockets = [], timers = new Map(), updates = [];
  let id = 0, time = 0;
  class Socket {
    constructor(url, protocol) { this.url = url; this.protocol = protocol; this.sent = []; sockets.push(this); }
    send(message) { this.sent.push(JSON.parse(message)); }
    close() { this.closed = true; }
    open() { this.onopen?.(); }
    message(data) { this.onmessage?.({ data }); }
    disconnect(code = 1006) { this.onclose?.({ code }); }
  }
  const connection = new Subscription(config(), update => updates.push(update), { origin: "https://ui.example",
    Socket, schedule: (callback, delay) => { const token = ++id; timers.set(token, { callback, delay }); return token; },
    cancel: token => timers.delete(token), now: () => time });
  const tick = () => { assert.equal(timers.size, 1); const [token, timer] = [...timers][0]; timers.delete(token); time += timer.delay; timer.callback(); };
  return { connection, sockets, timers, updates, tick, advance: value => { time += value; } };
}

test("unique-key finite UTF-8 JSON and bounded nesting", () => {
  for (const text of ['{"v":1,"v":1}', '{"x":{"a":1,"a":2}}', '1e999', 'NaN', '[1,]',
    '{"a":1,}', '"\\ud800"', '"\u0000"', '01', '{"a":true} trailing', "[".repeat(34) + "0" + "]".repeat(34)]) {
    assert.throws(() => strictJSON(text), text);
  }
  assert.throws(() => strictJSON('"🌱"', 4));
  assert.deepEqual(Array.from(strictJSON('[null, true, false, -1.2e3, "🌱\\\"\\\\"]')), [null, true, false, -1200, '🌱"\\']);
  assert.equal(strictJSON('{"__proto__":{"safe":true}}').__proto__.safe, true);
});

test("declarations refuse cross-origin, credentials and ambiguous limits", () => {
  for (const changes of [{ path: "https://other/notes/note-1" }, { path: "//other/notes/note-1" },
    { path: "/notes/note-1?token=x" }, { key: "note-2" }, { retryLimit: true }, { maxMessageBytes: 65537 },
    { fields: { key: "text", title: "number" } }, { v: 2 }]) assert.throws(() => configuration({ ...config(), ...changes }));
});

test("project snapshots and refuse identity, shape, binary and scalar drift", () => {
  const value = eventMessage(snapshot(), config());
  assert.equal(value.value.title, "Current");
  assert.equal(Object.hasOwn(value.value, "secret"), false);
  for (const data of [new Uint8Array([1]), '{}', snapshot().replace('"done":false', '"done":1'),
    snapshot().replace('"note-1"', '"note-2"'), snapshot("A".repeat(32)), snapshot("1".repeat(32), "x".repeat(4097)),
    snapshot().replace('"v":1', '"v":1,"v":1')]) assert.throws(() => eventMessage(data, config()));
});

test("one read-only subscribe and coalesced snapshots without queued work", () => {
  const f = fixture(), socket = f.sockets[0];
  assert.equal(socket.url, "wss://ui.example/api/notes/note-1");
  socket.open();
  assert.deepEqual(socket.sent, [{ v: 1, type: "subscribe", cursor: null }]);
  for (let i = 1; i <= 500; i++) socket.message(snapshot(i.toString(16).padStart(32, "0"), "Value " + i));
  assert.equal(f.connection.value.title, "Value 500");
  assert.equal(f.timers.size, 0);
  f.connection.stop();
  assert.equal(f.connection.value, null);
  assert.equal(socket.onmessage, null);
});

test("reconnect carries opaque cursor, suppresses duplicate and rejects old generations", () => {
  const f = fixture(), first = f.sockets[0];
  first.open(); first.message(snapshot());
  const stale = first.onmessage;
  first.disconnect();
  assert.equal(f.timers.size, 1);
  f.tick();
  const second = f.sockets[1]; second.open();
  assert.equal(second.sent[0].cursor, "1".repeat(32));
  stale({ data: snapshot("2".repeat(32), "Stale") });
  assert.equal(f.connection.value.title, "Current");
  second.message(snapshot("1".repeat(32), "Duplicate ignored"));
  assert.equal(f.connection.value.title, "Current");
  assert.equal(f.updates.at(-1).status, "live");
  second.message('{"v":1,"type":"resync"}');
  assert.equal(f.connection.value, null);
  assert.equal(f.updates.at(-1).status, "resyncing");
  assert.equal(f.timers.size, 1);
  second.message(snapshot("1".repeat(32), "Fresh"));
  assert.equal(f.connection.value.title, "Fresh");
  assert.equal(f.timers.size, 0);
  f.connection.stop();
});

test("retry exhaustion bounds rapid open-close and initial-snapshot stalls", () => {
  const f = fixture();
  f.sockets[0].open(); f.sockets[0].message(snapshot()); f.sockets[0].disconnect();
  f.tick();
  f.sockets[1].open(); f.sockets[1].message(snapshot()); f.sockets[1].disconnect();
  f.tick();
  f.sockets[2].open(); f.tick();
  assert.equal(f.connection.closed, true);
  assert.equal(f.timers.size, 0);
  assert.equal(f.updates.at(-1).status, "unavailable");
});

test("stable snapshot connection renews retry budget", () => {
  const f = fixture();
  f.sockets[0].disconnect(); f.tick();
  f.sockets[1].open(); f.sockets[1].message(snapshot());
  f.advance(30000); f.sockets[1].disconnect();
  assert.equal(f.connection.attempts, 1);
  f.connection.stop();
});

test("terminal denial, invalid subprotocol and malformed messages clear remote data", () => {
  for (const fail of [socket => socket.disconnect(1008), socket => socket.message('{}'),
    socket => { socket.protocol = "other"; socket.open(); }]) {
    const f = fixture(), socket = f.sockets[0]; socket.open(); socket.message(snapshot()); fail(socket);
    assert.equal(f.connection.closed, true);
    assert.equal(f.connection.value, null);
    assert.equal(f.timers.size, 0);
    assert.equal(socket.onclose, null);
  }
});

test("stop retires every timer and refuses retained callbacks without crossing editors", () => {
  const first = fixture(), second = fixture();
  const oldMessage = first.sockets[0].onmessage;
  first.sockets[0].disconnect();
  const oldTimer = [...first.timers.values()][0].callback;
  first.connection.stop();
  oldTimer(); oldMessage({ data: snapshot() });
  assert.equal(first.sockets.length, 1);
  assert.equal(first.timers.size, 0);
  assert.equal(first.connection.value, null);
  second.sockets[0].open(); second.sockets[0].message(snapshot());
  assert.equal(second.connection.value.title, "Current");
  second.connection.stop();
});
