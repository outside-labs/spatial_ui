from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import threading
import unittest

from spatial_ui import Field, FormSchema
from spatial_ui.rest import (
    BindingError, HTTPTransport, ResourceBinding, ResourceClient, ResourceSpec, Response, TransportError,
)


SCHEMA = FormSchema((Field("key"), Field("title"), Field("done", "checkbox", False, False)))
SPEC = ResourceSpec("/work-items", SCHEMA, ("key", "title", "done"))
ROW = {"key": "a", "title": "Initial", "done": False, "private": "never displayed"}


class FakeTransport:
    def __init__(self):
        self.calls = []
        self.response = Response(200, ROW, "1")

    def request(self, method, path, **kwargs):
        self.calls.append((method, path, kwargs))
        return self.response


class RestTests(unittest.TestCase):
    def setUp(self):
        self.transport = FakeTransport()
        self.binding = ResourceBinding(ResourceClient(self.transport, SPEC))
        self.binding.perform("load", key="a")

    def test_detached_projection_revision_and_checked_mutation(self):
        binding = self.binding
        self.assertNotIn("private", binding.committed)
        self.assertFalse(binding.dirty)
        binding.edit({"title": "Changed"})
        self.transport.response = Response(200, {**ROW, "title": "Changed"}, "2")
        self.assertEqual(binding.perform("update"), "saved")
        self.assertEqual(self.transport.calls[-1], ("PATCH", "/work-items/a", {"data": {"title": "Changed", "done": False}, "revision": "1"}))
        self.assertFalse(binding.dirty)
        self.assertEqual(binding.revision, "2")
        with self.assertRaises(TypeError):
            binding.draft["title"] = "mutation"

    def test_conflict_reload_preserves_draft_and_public_errors_hide_values(self):
        self.binding.edit({"title": "Local"})
        self.transport.response = Response(409, {"error": {"code": "revision_conflict"}})
        self.assertEqual(self.binding.perform("update"), "conflict")
        self.assertEqual(self.binding.committed["title"], "Initial")
        self.transport.response = Response(200, {**ROW, "title": "Remote"}, "2")
        self.binding.perform("load")
        self.assertEqual(self.binding.draft["title"], "Local")
        self.assertEqual(self.binding.committed["title"], "Remote")
        self.assertEqual(self.binding.revision, "2")
        self.transport.response = Response(422, {"error": {"message": "private value", "details": [
            {"path": ["title"], "code": "private value"}, {"path": ["private"], "code": "required"}]}})
        self.binding.perform("update")
        result = self.binding.result().as_dict()
        self.assertEqual(result["focus"], "title")
        self.assertNotIn("private", json.dumps(result))

    def test_duplicate_request_later_edits_cancellation_stale_completion_close(self):
        binding = self.binding
        binding.edit({"title": "Submitted"})
        pending = binding.begin("update")
        with self.assertRaises(BindingError):
            binding.begin("update")
        binding.edit({"title": "Later"})
        binding.complete(pending, Response(200, {**ROW, "title": "Submitted"}, "2"))
        self.assertEqual(binding.draft["title"], "Later")
        self.assertTrue(binding.dirty)
        canceled = binding.begin("update")
        binding.cancel()
        self.assertEqual(binding.status, "uncertain")
        self.assertFalse(binding.complete(canceled, Response(200, ROW, "3")))
        with self.assertRaises(BindingError):
            binding.begin("update")
        binding.perform("load")
        late = binding.begin("load")
        binding.close()
        self.assertFalse(binding.complete(late, Response(200, ROW, "4")))
        self.assertEqual(binding.status, "closed")

    def test_missing_revision_bad_identity_and_visibility_are_refused(self):
        for response in (Response(200, ROW), Response(200, ROW, "01"), Response(200, {**ROW, "key": "other"}, "1"),
                         Response(200, {"key": "a", "title": "x"}, "1")):
            self.transport.response = response
            self.assertEqual(self.binding.perform("load"), "error")
        self.binding.edit({"key": "changed"})
        with self.assertRaises(BindingError):
            self.binding.begin("update")

    def test_bounded_pagination_and_explicit_origins(self):
        self.transport.response = Response(200, [ROW])
        self.assertEqual(self.binding.client.list(limit=1)[0]["key"], "a")
        for kwargs in ({"offset": True}, {"limit": 1001}, {"offset": 10001}):
            with self.assertRaises(BindingError):
                self.binding.client.list(**kwargs)
        for url in ("http://example.com", "http://127.0.0.1/path", "https://user:pass@example.com", "//example.com", "ftp://example.com"):
            with self.assertRaises(BindingError):
                HTTPTransport(url)

    def test_real_http_bounds_headers_json_and_error_redaction(self):
        replies = [(b'{"key":"a","title":"x","done":false}', '"2"', "application/json"),
                   (b'{"x":1,"x":2}', None, "application/json"),
                   (b'{"x":1e999}', None, "application/json"),
                   (b'{}', 'W/"2"', "application/json"),
                   (b'x' * 101, None, "application/json"),
                   (b'private response', None, "text/html")]
        received = []
        class Handler(BaseHTTPRequestHandler):
            def do_PATCH(self):
                received.append((self.headers.get("If-Match"), self.rfile.read(int(self.headers["Content-Length"]))))
                body, etag, content_type = replies.pop(0)
                self.send_response(200)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                if etag:
                    self.send_header("ETag", etag)
                self.end_headers()
                self.wfile.write(body)
            def log_message(self, *args):
                pass
        with HTTPServer(("127.0.0.1", 0), Handler) as server:
            thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
            thread.start()
            try:
                transport = HTTPTransport(f"http://127.0.0.1:{server.server_port}", max_body=100)
                self.assertEqual(transport.request("PATCH", "/work-items/a", data={"title": "x"}, revision="1").revision, "2")
                self.assertEqual(received[0][0], '"1"')
                for _ in range(5):
                    with self.assertRaises(TransportError) as caught:
                        transport.request("PATCH", "/work-items/a", data={"title": "x"}, revision="1")
                    self.assertNotIn("private", str(caught.exception))
            finally:
                server.shutdown()
                thread.join(3)
