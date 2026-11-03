import asyncio
from dataclasses import dataclass
import json
import re
import threading
import unittest

from spatial_ui import App, ASGIApp
from spatial_ui.forms import MAX_BODY
from test_form_sessions import Editor, event


@dataclass
class Result:
    status: int
    headers: dict
    body: bytes
    reads: int = 0

    @property
    def text(self):
        return self.body.decode()

    @property
    def json(self):
        return json.loads(self.body)


async def request(app, path="/", method="GET", *, headers=(), body=b"", events=None,
                  receive=None, query=b"", root="", send=None):
    queue = asyncio.Queue()
    for item in events if events is not None else [{"type": "http.request", "body": body}]:
        queue.put_nowait(item)
    messages, reads = [], 0
    async def read():
        nonlocal reads
        reads += 1
        return await (receive() if receive else queue.get())
    async def write(message):
        messages.append(message)
        if send:
            await send(message)
    await app({"type": "http", "method": method, "path": path, "root_path": root, "query_string": query,
               "headers": [(b"host", b"example.test"), *headers]}, read, write)
    if not messages:
        return Result(0, {}, b"", reads)
    return Result(messages[0]["status"], dict(messages[0]["headers"]), messages[1]["body"], reads)


class Client:
    def __init__(self, app):
        self.app, self.cookie, self.token = app, b"", b""

    async def page(self):
        response = await request(self.app, headers=[(b"cookie", self.cookie)])
        if response.status == 200:
            self.cookie = response.headers[b"set-cookie"].split(b";", 1)[0]
            self.token = re.search(rb'name="sui-token" content="([^"]+)"', response.body)[1]
        return response

    async def post(self, payload, **kwargs):
        return await request(self.app, "/_spatial/event", "POST", headers=[
            (b"cookie", self.cookie), (b"x-spatial-token", self.token),
            (b"origin", b"https://example.test"), (b"content-type", b"application/json")],
            body=json.dumps(payload).encode(), **kwargs)


class ASGITests(unittest.IsolatedAsyncioTestCase):
    async def test_pages_assets_sessions_forms_stale_bindings_and_visibility(self):
        async with ASGIApp(App(Editor, title="<test>"), origin="https://example.test") as app:
            first, second = Client(app), Client(app)
            a, b = await first.page(), await second.page()
            self.assertEqual(a.status, 200)
            self.assertIn("<title>&lt;test&gt;</title>", a.text)
            self.assertNotEqual(first.cookie, second.cookie)
            self.assertIn(b"; Secure", a.headers[b"set-cookie"])
            self.assertTrue(first.cookie.startswith(b"__Host-"))
            self.assertIn(b"HttpOnly; SameSite=Strict", a.headers[b"set-cookie"])
            self.assertIn(b"frame-ancestors 'none'", a.headers[b"content-security-policy"])
            invalid = event(a.text, {"title": "", "done": False})
            response = await first.post(invalid)
            self.assertEqual(response.json["form"]["focus"], "title")
            self.assertEqual((await first.post(invalid)).status, 409)
            a = await first.page()
            saved = await first.post(event(a.text, {"title": "Garden 🌱", "done": True}))
            self.assertEqual(saved.json["form"]["status"], "saved")
            self.assertIn("Saves: 1", saved.json["markup"])
            self.assertIn("Saves: 0", (await second.page()).text)
            for path in ("/_spatial/runtime.js", "/_spatial/theme.css"):
                self.assertEqual((await request(app, path)).status, 200)
                head = await request(app, path, "HEAD")
                self.assertEqual((head.status, head.body), (200, b""))
            self.assertIn("ResizeObserver", (await request(app, "/_spatial/runtime.js")).text)

    async def test_input_origins_headers_chunks_deadlines_disconnect_and_bounds(self):
        async with ASGIApp(App(Editor), origin="https://example.test", body_timeout=0.02) as app:
            client = Client(app)
            page = await client.page()
            for kwargs in ({"query": b"x=1"}, {"root": "/prefix"}, {"path": "/private"}):
                self.assertEqual((await request(app, **kwargs)).status, 404)
            self.assertEqual((await request(app, method="PUT")).status, 405)
            self.assertEqual((await request(app, headers=[(b"Host", b"evil")])).status, 400)
            bad_host = await request(app, headers=[(b"origin", b"https://evil")], method="POST", path="/_spatial/event")
            self.assertEqual((bad_host.status, bad_host.reads), (403, 0))
            payload = event(page.text, {"title": "Chunks", "done": False})
            raw = json.dumps(payload).encode()
            response = await client.post(payload, events=[{"type": "http.request", "body": raw[:5], "more_body": True},
                                                         {"type": "http.request", "body": raw[5:]}])
            self.assertEqual(response.status, 200)
            self.assertEqual((await request(app, body=b"x"*(MAX_BODY+1))).status, 413)
            self.assertEqual((await request(app, headers=[(b"content-length", b"1")])).status, 400)
            self.assertEqual((await request(app, events=[])).status, 408)
            self.assertEqual((await request(app, events=[{"type": "http.disconnect"}])).status, 0)
            self.assertEqual((await request(app, headers=[(b"cookie", client.cookie+b"; "+client.cookie)])).status, 400)

    async def test_expiry_capacity_restart_failure_redaction_and_response_bound(self):
        now = [0]
        app = ASGIApp(App(Editor, max_sessions=1, session_ttl=2, clock=lambda: now[0]), origin="https://example.test")
        async with app:
            first = Client(app)
            page = await first.page()
            old_payload = event(page.text, {"title": "Expired", "done": False})
            self.assertEqual((await Client(app).page()).status, 503)
            now[0] = 2
            self.assertEqual((await first.post(old_payload)).status, 403)
            self.assertEqual((await first.page()).status, 200)
        self.assertEqual(app._app._sessions, {})
        self.assertIsNone(app._executor)
        self.assertEqual((await request(app)).status, 503)
        async with app:
            self.assertEqual((await first.post(old_payload)).status, 403)
            self.assertIn("Saves: 0", (await first.page()).text)
        def failure():
            raise RuntimeError("synthetic private failure")
        async with ASGIApp(App(failure), origin="https://example.test") as broken:
            response = await request(broken)
            self.assertEqual(response.status, 500)
            self.assertNotIn("synthetic", response.text)
            self.assertEqual(broken._app._sessions, {})
        async with ASGIApp(App(Editor, stylesheet="x"*2048), origin="https://example.test", max_response_size=1024) as bounded:
            response = await request(bounded, "/_spatial/theme.css")
            self.assertEqual(response.status, 500)
            self.assertNotIn("x"*20, response.text)

    async def test_canceled_dispatched_callback_retains_capacity_and_close_drains(self):
        entered, release = threading.Event(), threading.Event()
        calls = []
        class Blocking(Editor):
            def handle(self, event):
                entered.set()
                if not release.wait(5):
                    raise TimeoutError
                calls.append(event.fields["title"])
                return super().handle(event)
        app = ASGIApp(App(Blocking), origin="https://example.test", pending_limit=1)
        await app.__aenter__()
        try:
            client = Client(app)
            page = await client.page()
            task = asyncio.create_task(client.post(event(page.text, {"title": "Accepted", "done": False})))
            async def started():
                while not entered.is_set():
                    await asyncio.sleep(0.001)
            await asyncio.wait_for(started(), 2)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            self.assertEqual((await request(app)).status, 503)
            closing = asyncio.create_task(app.aclose())
            await asyncio.sleep(0)
            closing.cancel()
            await asyncio.sleep(0)
            self.assertFalse(closing.done())
            release.set()
            with self.assertRaises(asyncio.CancelledError):
                await closing
            self.assertEqual(calls, ["Accepted"])
            self.assertEqual(app._app._sessions, {})
            self.assertEqual(app._tickets, set())
            self.assertIsNone(app._executor)
            self.assertFalse(any(thread.name.startswith("spatial-ui") for thread in threading.enumerate()))
        finally:
            release.set()
            await app.aclose()

    async def test_admission_includes_response_sends_and_lifespan_is_owned(self):
        app = ASGIApp(App(Editor), origin="https://example.test", pending_limit=1)
        incoming, outgoing = asyncio.Queue(), asyncio.Queue()
        lifespan = asyncio.create_task(app({"type": "lifespan"}, incoming.get, outgoing.put))
        await incoming.put({"type": "lifespan.startup"})
        self.assertEqual((await outgoing.get())["type"], "lifespan.startup.complete")
        blocked, release = asyncio.Event(), asyncio.Event()
        async def slow_send(message):
            blocked.set()
            await release.wait()
        request_task = asyncio.create_task(request(app, send=slow_send))
        try:
            await asyncio.wait_for(blocked.wait(), 2)
            self.assertEqual((await request(app)).status, 503)
        finally:
            release.set()
            await request_task
            await incoming.put({"type": "lifespan.shutdown"})
            self.assertEqual((await outgoing.get())["type"], "lifespan.shutdown.complete")
            await lifespan

    async def test_queued_cancellation_prevents_component_construction(self):
        entered, release = threading.Event(), threading.Event()
        created = []
        class Blocking(Editor):
            def __init__(self):
                super().__init__()
                created.append(self)
            def handle(self, value):
                entered.set()
                if not release.wait(5):
                    raise TimeoutError
                return super().handle(value)
        async with ASGIApp(App(Blocking), origin="https://example.test", pending_limit=2) as app:
            client = Client(app)
            page = await client.page()
            first = asyncio.create_task(client.post(event(page.text, {"title": "First", "done": False})))
            queued = None
            try:
                async def started():
                    while not entered.is_set():
                        await asyncio.sleep(0.001)
                await asyncio.wait_for(started(), 2)
                queued = asyncio.create_task(request(app))
                async def queued_work():
                    while not any(t.future and not t.future.running() and not t.future.done() for t in app._tickets):
                        await asyncio.sleep(0.001)
                await asyncio.wait_for(queued_work(), 2)
                queued.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await queued
            finally:
                release.set()
                await first
                if queued and not queued.done():
                    queued.cancel()
                    await asyncio.gather(queued, return_exceptions=True)
            self.assertEqual(len(created), 1)
            self.assertEqual(created[0].saves, 1)

    async def test_disconnect_after_dispatch_does_not_send_or_interrupt_callback(self):
        entered, release = threading.Event(), threading.Event()
        values = []
        class Blocking(Editor):
            def handle(self, value):
                entered.set()
                if not release.wait(5):
                    raise TimeoutError
                values.append(value.fields["title"])
                return super().handle(value)
        async with ASGIApp(App(Blocking), origin="https://example.test") as app:
            client = Client(app)
            page = await client.page()
            payload = event(page.text, {"title": "Committed", "done": False})
            incoming = asyncio.Queue()
            incoming.put_nowait({"type": "http.request", "body": json.dumps(payload).encode()})
            task = asyncio.create_task(client.post(payload, receive=incoming.get))
            try:
                async def started():
                    while not entered.is_set():
                        await asyncio.sleep(0.001)
                await asyncio.wait_for(started(), 2)
                incoming.put_nowait({"type": "http.disconnect"})
                self.assertEqual((await asyncio.wait_for(task, 2)).status, 0)
                self.assertEqual(len(app._tickets), 1)
            finally:
                release.set()
                await task
        self.assertEqual(values, ["Committed"])

    async def test_duplicate_lifespan_does_not_close_existing_owner(self):
        app = ASGIApp(App(Editor), origin="https://example.test")
        async with app:
            messages = []
            async def startup():
                return {"type": "lifespan.startup"}
            async def output(message):
                messages.append(message)
            await app({"type": "lifespan"}, startup, output)
            self.assertEqual(messages[0]["type"], "lifespan.startup.failed")
            self.assertEqual((await request(app)).status, 200)

    def test_configuration_is_explicit_and_has_no_worker_or_session(self):
        for origin in ("http://public.example", "https://example.test/", "https://user@example.test", "https://example.test?q=1",
                       "https://example.test:443", "https://EXAMPLE.test", "https://example.test\n"):
            with self.assertRaises(ValueError, msg=origin):
                ASGIApp(App(Editor), origin=origin)
        for kwargs in ({"pending_limit": True}, {"pending_limit": 0}, {"body_timeout": float("inf")}, {"max_response_size": 0}):
            with self.assertRaises(ValueError):
                ASGIApp(App(Editor), origin="https://example.test", **kwargs)
        app = ASGIApp(App(Editor), origin="http://127.0.0.1:8080")
        self.assertIsNone(app._executor)
        self.assertEqual(app._app._sessions, {})
