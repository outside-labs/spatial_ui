"""Optional integration against the immutable checked REST service profile."""

import asyncio
from contextlib import contextmanager
from pathlib import Path
from queue import Queue
from tempfile import TemporaryDirectory
import threading
import unittest

from spatial_ui import Field, FormSchema
from spatial_ui.resource_form import ResourceForm
from spatial_ui.rest import HTTPTransport, ResourceClient, ResourceSpec

try:
    from compose_application.reference import reference_application
    from expose_api import serve
except ImportError:
    reference_application = None


@contextmanager
def reference_server(database):
    started, stop = Queue(), threading.Event()
    async def run():
        try:
            async with reference_application(database) as service:
                async with serve(service.app, port=0) as server:
                    started.put(server.sockets[0].getsockname()[1])
                    await asyncio.to_thread(stop.wait)
        except Exception as error:
            started.put(error)
    thread = threading.Thread(target=lambda: asyncio.run(run()), daemon=True)
    thread.start()
    try:
        port = started.get(timeout=10)
        if isinstance(port, Exception):
            raise port
        yield port
    finally:
        stop.set()
        thread.join(10)
        if thread.is_alive():
            raise RuntimeError("Reference service did not close.")


def reference_client(port):
    schema = FormSchema((Field("key"), Field("title"), Field("done", "checkbox", False, False)))
    return ResourceClient(HTTPTransport(f"http://127.0.0.1:{port}"),
                          ResourceSpec("/work-items", schema, ("key", "title", "done")))


@unittest.skipIf(reference_application is None, "install the reference-tests development group")
class ReferenceFormTests(unittest.TestCase):
    def test_actual_checked_rest_validation_conflict_merge_delete_and_restart(self):
        with TemporaryDirectory() as directory:
            database = Path(directory) / "work.sqlite3"
            with reference_server(database) as port:
                client = reference_client(port)
                first, second = ResourceForm(client), ResourceForm(client)
                def submit(form, **values):
                    from spatial_ui import FormEvent
                    return form.handle(FormEvent("submit", {**form.binding.draft, "operation": "save", **values}))
                self.assertEqual(submit(first, key="a", title="Initial").status, "saved")
                self.assertEqual(submit(second, key="a", operation="load").status, "loaded")
                self.assertEqual(submit(first, title="Remote").status, "saved")
                self.assertEqual(submit(second, title="Local").status, "conflict")
                self.assertEqual(second.binding.draft["title"], "Local")
                self.assertEqual(submit(second, operation="load").status, "loaded")
                self.assertEqual(second.binding.committed["title"], "Remote")
                self.assertEqual(second.binding.draft["title"], "Local")
                self.assertEqual(submit(second).status, "saved")
                self.assertEqual(second.binding.revision, "3")
                self.assertEqual(submit(second, title="x" * 201).status, "invalid")
                self.assertEqual(client.list()[0]["title"], "Local")
            with reference_server(database) as port:
                form = ResourceForm(reference_client(port))
                self.assertEqual(submit(form, key="a", operation="load").status, "loaded")
                self.assertEqual(form.binding.committed["title"], "Local")
                self.assertEqual(form.binding.revision, "3")
                self.assertEqual(submit(form, operation="delete").status, "deleted")
                self.assertEqual(form.binding.client.list(), ())
