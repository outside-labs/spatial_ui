"""Chromium acceptance against the selected protected native event implementation."""

import asyncio
from contextlib import AsyncExitStack, contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
import json
import re
from pathlib import Path
from queue import Queue
from tempfile import TemporaryDirectory
import threading
import unittest

from playwright.sync_api import sync_playwright, expect
from expose_api import App as API, Principal
from expose_api.backpack import BackpackStore
from spatial_ui import App, ASGIApp, Field, FormSchema
from spatial_ui.live import LiveConfig
from spatial_ui.resource_form import ResourceForm
from spatial_ui.rest import HTTPTransport, ResourceClient, ResourceSpec
from websocket_host import WireHost, lifespan


@dataclass
class Note:
    key: str
    owner: str = field(default="alice", metadata={"expose_api": {"write_only": True}})
    title: str = "Initial"
    done: bool = False


@contextmanager
def running_live():
    ready, failures, receipts = Queue(), [], []
    with TemporaryDirectory(prefix="live-browser-") as directory:
        def worker():
            async def run():
                host = WireHost()
                async with host.listening(), AsyncExitStack() as cleanup:
                    expected, returned = ContextVar("expected", default=None), ContextVar("returned", default=None)
                    class CheckedStore(BackpackStore):
                        async def get(self, key):
                            row = await self.get_row(key)
                            returned.set(row.revision)
                            return row.value
                        async def update(self, key, transform):
                            row = await self.update_checked(key, transform=transform, expected_revision=expected.get())
                            returned.set(row.revision)
                            return row.value
                    store = await CheckedStore.open(Note, Path(directory) / "notes.sqlite3", namespace="live",
                        table="app_notes", type_key="live.note", id_field="key", events=4, event_namespace="note_events")
                    await cleanup.enter_async_context(store)
                    identities = {"browser-alice": Principal("alice", scopes=frozenset({"notes"}))}
                    def authenticate(request):
                        authorization = request.header("authorization")
                        if authorization:
                            return identities.get(authorization.removeprefix("Bearer ")) if authorization.startswith("Bearer ") else None
                        cookies = [part.strip().partition("=")[2] for part in (request.header("cookie") or "").split(";")
                                   if part.strip().partition("=")[0] == "identity"]
                        return identities.get(cookies[0]) if len(cookies) == 1 else None
                    api = API(authenticate=authenticate, auth_challenge="Custom")
                    resource = api.include(Note, store=store, path="/api/notes", id_field="key",
                        authorize=lambda request: "notes" in request.principal.scopes,
                        record_policy=lambda principal, operation, value: principal.subject == value.owner)
                    await resource.create({"key": "note-1"})
                    await resource.create({"key": "private", "owner": "bob", "title": "Hidden"})
                    api.subscribe(resource, origins=(host.origin,), hosts=(host.origin.split("://", 1)[1],),
                        connections=16, poll_interval=0.02, max_message_bytes=65536)
                    schema = FormSchema((Field("key"), Field("title"), Field("done", "checkbox", False, False)))
                    client = ResourceClient(HTTPTransport(host.origin, headers=(("Authorization", "Bearer browser-alice"),)),
                        ResourceSpec("/api/notes", schema, ("key", "title", "done")))
                    def factory():
                        form = ResourceForm(client, live=LiveConfig("/api/notes", retry_delay_ms=500))
                        if form.binding.perform("load", key="note-1") != "loaded":
                            raise RuntimeError("Browser fixture failed to load its record")
                        return form
                    adapter = ASGIApp(App(factory, stylesheet="body{font:16px system-ui;margin:1rem}input,select{max-width:100%;box-sizing:border-box}dd{overflow-wrap:anywhere}"), origin=host.origin)
                    async def route(scope, receive, send):
                        if scope["path"].startswith("/api/"):
                            if scope["type"] == "websocket":
                                await api(scope, receive, send)
                                return
                            revision = dict(scope.get("headers", ())).get(b"if-match", b"")
                            match = re.fullmatch(rb'"([1-9][0-9]{0,18})"', revision)
                            if scope["method"] == "PATCH" and match is None:
                                raise ValueError("The browser fixture requires a canonical revision")
                            token, result_token = expected.set(match[1].decode() if match else None), returned.set(None)
                            async def response(event):
                                if event["type"] == "http.response.start" and 200 <= event["status"] < 300 and returned.get():
                                    event = {**event, "headers": [*event["headers"], (b"etag", ('"'+returned.get()+'"').encode())]}
                                await send(event)
                            try:
                                await api(scope, receive, response)
                            finally:
                                expected.reset(token); returned.reset(result_token)
                        else:
                            # The fixture UI is an explicit Alice-only credential bridge.
                            cookies = dict(scope.get("headers", ())).get(b"cookie", b"")
                            if b"identity=browser-alice" not in [part.strip() for part in cookies.split(b";")]:
                                await send({"type": "http.response.start", "status": 403, "headers": [(b"content-length", b"0")]})
                                await send({"type": "http.response.body", "body": b""})
                                return
                            await adapter(scope, receive, send)
                    host.app = route
                    stop = asyncio.Event()
                    async with lifespan(api), adapter:
                        ready.put((asyncio.get_running_loop(), stop, host, resource, identities))
                        await stop.wait()
                    receipts.append({"sockets": len(api._sockets.tasks), "sessions": len(adapter._app._sessions),
                        "requests": len(adapter._tickets), "worker_closed": adapter._executor is None})
            try:
                asyncio.run(run())
            except BaseException as error:
                failures.append(error)
                ready.put(None)
        thread = threading.Thread(target=worker, name="live-browser-host")
        thread.start()
        state = None
        try:
            state = ready.get(timeout=15)
            if state is None:
                raise RuntimeError("Live browser fixture failed") from failures[0]
            class Control:
                origin = state[2].origin
                def run(self, coroutine):
                    return asyncio.run_coroutine_threadsafe(coroutine, state[0]).result(5)
                def update(self, title):
                    return self.run(state[3].update("note-1", {"title": title}))
                def drop_and_expire(self):
                    async def change():
                        state[2].drop()
                        for index in range(8):
                            await state[3].update("note-1", {"title": f"Recovery {index}"})
                    self.run(change())
                def revoke(self):
                    async def change():
                        state[4].clear()
                    self.run(change())
                def resynced(self):
                    return any(json.loads(message).get("type") == "resync" for message in state[2].messages)
            yield Control()
        finally:
            if state:
                state[0].call_soon_threadsafe(state[1].set)
            thread.join(15)
            if thread.is_alive():
                raise RuntimeError("Live browser host did not stop")
            if failures:
                raise RuntimeError("Live browser host failed") from failures[0]
            if receipts != [{"sockets": 0, "sessions": 0, "requests": 0, "worker_closed": True}]:
                raise AssertionError(f"Live browser retained owned state: {receipts}")


INSTRUMENT = """() => {
  const NativeSocket = window.WebSocket;
  window.liveSockets = [];
  window.liveTrace = [];
  window.WebSocket = class extends NativeSocket {
    constructor(...args) { super(...args); window.liveSockets.push(this);
      this.addEventListener('close', event => liveTrace.push(['close',event.code]));
      this.addEventListener('message', event => liveTrace.push(['message',JSON.parse(event.data).type]));
    }
  };
  window.liveTimers = new Set();
  const schedule = window.setTimeout, cancel = window.clearTimeout;
  window.setTimeout = (callback, delay, ...args) => {
    const live = new Error().stack.includes('/_spatial/live.js');
    const id = schedule(() => { window.liveTimers.delete(id); callback(...args); }, delay);
    if (live) window.liveTimers.add(id);
    return id;
  };
  window.clearTimeout = id => { window.liveTimers.delete(id); cancel(id); };
}"""


class BrowserLive(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.playwright = sync_playwright().start()
        cls.browser = cls.playwright.chromium.launch()

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.playwright.stop()
        Path("live-browser-receipt.json").write_text(json.dumps({"profile": "spatial-live/1", "browser": "Chromium",
            "api_source": "22e7afb7638440b52c08affa4c4c3c1da1bccfaf", "widths": [390, 1280],
            "cases": ["cookies", "independent-contexts", "dirty-remote", "focus-selection", "composition",
                "conflict-merge", "revocation", "reconnect-resync", "stale-generation", "stop-reload"],
            "shutdown": {"sockets": 0, "sessions": 0, "requests": 0, "worker_closed": True}}, indent=2) + "\n")

    def context(self, origin, width=1280):
        context = self.browser.new_context(viewport={"width": width, "height": 800}, reduced_motion="reduce")
        context.add_cookies([{"name": "identity", "value": "browser-alice", "url": origin, "httpOnly": True, "sameSite": "Strict"}])
        context.add_init_script("(" + INSTRUMENT + ")()")
        self.addCleanup(context.close)
        return context

    def loaded(self, context, origin):
        page = context.new_page()
        page.goto(origin)
        expect(page.locator("[data-sui-live]")).to_have_attribute("data-sui-live-state", "live")
        return page

    def apply(self, page, operation):
        page.locator('[name="operation"]').select_option(operation)
        page.get_by_role("button", name="Apply", exact=True).click()

    def test_dirty_remote_focus_composition_conflict_merge_and_isolation(self):
        for width in (390, 1280):
            with self.subTest(width=width), running_live() as service:
                first, second = self.context(service.origin, width), self.context(service.origin)
                try:
                    page, other = self.loaded(first, service.origin), self.loaded(second, service.origin)
                    errors = []
                    page.on("pageerror", lambda error: errors.append(str(error)))
                    title = page.locator('[name="title"]')
                    title.fill("Local draft")
                    expect(page.locator("#sui-status")).to_have_text("Unsaved changes.")
                    title.evaluate("node => node.setSelectionRange(2, 5)")
                    service.update("Remote 🌱")
                    expect(page.locator("[data-live-value]")).to_contain_text("Remote 🌱")
                    expect(title).to_have_value("Local draft")
                    expect(title).to_be_focused()
                    self.assertEqual(title.evaluate("node => [node.selectionStart,node.selectionEnd]"), [2, 5])
                    expect(page.locator("section > p").filter(has_text="Revision:")).to_have_text("Revision: 1")
                    expect(other.locator('[name="title"]')).to_have_value("Initial")
                    self.apply(page, "save")
                    expect(page.locator("#sui-status")).to_contain_text("record changed")
                    self.apply(page, "load")
                    expect(page.locator("#sui-status")).to_have_text("Loaded.")
                    expect(title).to_have_value("Local draft")
                    self.apply(page, "save")
                    expect(page.locator("#sui-status")).to_have_text("Saved.")
                    expect(other.locator("[data-live-value]")).to_contain_text("Local draft")
                    title.fill("")
                    self.apply(page, "save")
                    expect(title).to_be_focused()
                    expect(title).to_have_attribute("aria-invalid", "true")
                    title.evaluate('''node => { node.dispatchEvent(new CompositionEvent("compositionstart",{bubbles:true}));
                      node.value="日本語"; node.dispatchEvent(new InputEvent("input",{bubbles:true,isComposing:true})); }''')
                    service.update("During composition")
                    expect(page.locator("[data-live-value]")).to_contain_text("During composition")
                    expect(title).to_have_value("日本語")
                    title.evaluate('node => node.dispatchEvent(new CompositionEvent("compositionend",{bubbles:true}))')
                    expect(page.locator("#sui-status")).to_have_text("Unsaved changes.")
                    self.assertFalse(page.evaluate("document.documentElement.scrollWidth > innerWidth"))
                    self.assertEqual(errors, [])
                    self.assertNotIn("owner", page.locator("[data-live-value]").inner_text())
                finally:
                    first.close(); second.close()

    def test_reconnect_resync_stale_generation_revocation_and_stop_reload(self):
        with running_live() as service:
            context = self.context(service.origin)
            try:
                page = self.loaded(context, service.origin)
                page.evaluate("() => { window.oldMessage = liveSockets.at(-1).onmessage; }")
                service.drop_and_expire()
                try:
                    expect(page.locator("[data-live-value]")).to_contain_text("Recovery 7")
                except AssertionError:
                    self.fail(f"Recovery failed: {page.evaluate('liveTrace')}")
                self.assertTrue(service.resynced())
                page.evaluate('''() => oldMessage({data:JSON.stringify({v:1,type:"snapshot",cursor:"f".repeat(32),
                  value:{key:"note-1",title:"Stale forbidden",done:false}})})''')
                expect(page.locator("[data-live-value]")).not_to_contain_text("Stale forbidden")
                page.locator("#sui-app").dispatch_event("sui:stop")
                page.wait_for_function("() => liveTimers.size === 0 && liveSockets.every(s => s.readyState === 3 && s.onmessage === null && s.onclose === null)")
                version = page.locator("#sui-app").get_attribute("data-version")
                page.locator("form").dispatch_event("submit")
                self.assertEqual(page.locator("#sui-app").get_attribute("data-version"), version)
                page.reload()
                expect(page.locator("[data-sui-live]")).to_have_attribute("data-sui-live-state", "live")
                service.revoke()
                expect(page.locator("[data-sui-live]")).to_have_attribute("data-sui-live-state", "unavailable")
                expect(page.locator("[data-live-value]")).to_have_text("")
                page.wait_for_function("() => liveTimers.size === 0 && liveSockets.every(s => s.onmessage === null)")
            finally:
                context.close()

    def test_manager_capacity_removal_and_denied_record_admission(self):
        with running_live() as service:
            context = self.context(service.origin)
            try:
                page = self.loaded(context, service.origin)
                result = page.evaluate('''async () => {
                  const {LiveConnections} = await import('/_spatial/live.js');
                  const root=document.createElement('div'), sockets=[];
                  for(let i=0;i<17;i++) root.append(document.querySelector('[data-sui-live]').cloneNode(true));
                  class Socket { constructor(){this.protocol='expose-api.events.v1';sockets.push(this)} close(){this.closed=true} }
                  const manager=new LiveConnections(root,{Socket});
                  manager.scan(); manager.scan();
                  const admitted=manager.entries.size, unavailable=root.lastElementChild.dataset.suiLiveState;
                  root.replaceChildren(); manager.scan();
                  const retired=manager.entries.size===0 && sockets.every(s=>s.closed && s.onmessage===null);
                  manager.stop();
                  return {admitted,created:sockets.length,unavailable,retired};
                }''')
                self.assertEqual(result, {"admitted": 16, "created": 16, "unavailable": "unavailable", "retired": True})
                for key in ("private", "missing"):
                    code = page.evaluate('''path => new Promise(resolve => {
                      const socket=new WebSocket(location.origin.replace('http','ws')+'/api/notes/'+path,'expose-api.events.v1');
                      socket.onclose=event=>resolve(event.code);
                    })''', key)
                    self.assertEqual(code, 1006)
            finally:
                context.close()
