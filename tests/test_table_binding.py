import copy
import threading
import time
import unittest

from spatial_ui import HTTPTableClient, Session, TableBinding, TableBrowser, TableColumn, TableError, TablePage, TableRow
from spatial_ui.rest import Response


class DatasetClient:
    table_id, schema_version, mode = "records", "1", "Remote dataset query"
    columns = (TableColumn("title", "Title", True, True), TableColumn("rank", "Rank", True))

    def __init__(self):
        self.data = [(f"row-{i}", f"Title {i}", str(i)) for i in range(8)]
        self.queries = []
        self.closed = False

    def query(self, query, cancel):
        self.queries.append(copy.deepcopy(query))
        rows = list(self.data)
        if query["search"]:
            rows = [r for r in rows if query["search"]["term"] in r[1]]
        for order in reversed(query["order"]):
            rows.sort(key=lambda row: row[1 if order["column_id"] == "title" else 2], reverse=order["descending"])
        start = int(query["cursor"] or "0")
        end = start + query["limit"]
        selected = query["columns"]
        return TablePage(self.table_id, self.schema_version, tuple(c for name in selected for c in self.columns if c.id == name),
            tuple(TableRow(row[0], tuple(row[1 if name == "title" else 2] for name in selected), "1") for row in rows[start:end]),
            query["generation"], str(end) if end < len(rows) else None)

    def close(self):
        self.closed = True


def settle(binding):
    deadline = time.monotonic() + 5
    while binding.status == "loading":
        binding.poll()
        if time.monotonic() >= deadline:
            raise AssertionError("query did not settle")
        time.sleep(.001)


class TableBindingTests(unittest.TestCase):
    def test_dataset_controls_independent_sessions_and_no_mutations(self):
        client = DatasetClient()
        original = copy.deepcopy(client.data)
        one, two = TableBinding(client, page_size=2), TableBinding(client, page_size=2)
        try:
            settle(one); settle(two)
            one.next(); settle(one)
            self.assertEqual(one.page.rows[0].key, "row-2")
            one.sort("title"); settle(one)
            one.sort("title"); settle(one)
            self.assertEqual(one.page.rows[0].key, "row-7")
            self.assertEqual(one.cursors, [None])
            one.search("Title 5"); settle(one)
            one.project(["rank", "title"]); settle(one)
            self.assertEqual(one.page.rows[0].values, ("5", "Title 5"))
            self.assertEqual(two.page.rows[0].key, "row-0")
            self.assertEqual(client.data, original)
            self.assertEqual(one.page.rows[0].revision, "1")
        finally:
            one.close(); two.close()
        self.assertFalse(client.closed)

    def test_superseded_requests_source_replacement_and_close_drain(self):
        entered, release = threading.Event(), threading.Event()
        original = DatasetClient()
        query = original.query
        def hold(descriptor, cancel):
            entered.set()
            if not release.wait(5):
                raise TimeoutError
            return query(descriptor, cancel)
        original.query = hold
        binding = TableBinding(original, page_size=2, owns_client=True)
        try:
            self.assertTrue(entered.wait(2))
            first = binding._active
            for term in ("Title 1", "Title 2", "Title 5"):
                binding.search(term)
            self.assertTrue(first[2].is_set())
            self.assertEqual(binding._desired["search"]["term"], "Title 5")
            replacement = DatasetClient()
            replacement.table_id = "replacement"
            binding.replace(replacement)
            release.set()
            settle(binding)
            self.assertEqual(binding.page.table_id, "replacement")
            self.assertEqual(len(original.queries), 1)
            self.assertTrue(original.closed)
        finally:
            release.set()
            binding.close()
        self.assertTrue(replacement.closed)
        self.assertEqual(binding.status, "closed")
        self.assertFalse(any(t.is_alive() for t in binding._worker._threads))

    def test_session_close_and_error_refresh_preserve_selection(self):
        client = DatasetClient()
        component = TableBrowser(client, page_size=2)
        session = Session(component)
        settle(component.binding)
        session.render()
        component.view.select("row-0", "title")
        original = client.query
        client.query = lambda query, cancel: (_ for _ in ()).throw(TableError("private failure"))
        component.binding.refresh(); settle(component.binding)
        self.assertEqual(component.binding.status, "error")
        self.assertNotIn("private failure", session.render().markup)
        self.assertEqual(component.view.selected, ("row-0", "title"))
        client.query = original
        component.binding.refresh(); settle(component.binding)
        self.assertEqual(component.binding.status, "ready")
        session.close(); session.close()
        self.assertTrue(component.binding.closed)

    def test_http_admission_rejects_hidden_values_stale_generations_and_wrong_types(self):
        schema = dict(contract="tabular/1", table_id="records", schema_version="1", columns=[dict(id="title", label="Title", type="text", nullable=False, visible=True, sortable=True, searchable=True, writable=False)])
        class Transport:
            def request(self, method, path, **kwargs):
                return Response(200, self.data)
        transport = Transport()
        client = HTTPTableClient(transport, "/rows", schema)
        descriptor = dict(generation=1, columns=["title"], limit=2)
        payload = dict(contract="tabular/1", table_id="records", schema_version="1", generation=1, columns=["title"],
                       rows=[dict(key="row-1", revision="1", values=dict(title="Alpha"))], next_cursor=None)
        for change in ("valid", "hidden", "generation", "type", "revision"):
            transport.data = copy.deepcopy(payload)
            if change == "hidden": transport.data["rows"][0]["values"]["owner"] = "private"
            if change == "generation": transport.data["generation"] = 2
            if change == "type": transport.data["rows"][0]["values"]["title"] = 1
            if change == "revision": transport.data["rows"][0]["revision"] = 1
            if change == "valid":
                self.assertEqual(client.query(descriptor, threading.Event()).rows[0].values, ("Alpha",))
            else:
                with self.subTest(change=change), self.assertRaises(TableError):
                    client.query(descriptor, threading.Event())


class TableLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_native_close_requires_origin_session_and_token_then_drains(self):
        from spatial_ui import App, ASGIApp
        from test_asgi import Client, request
        components = []
        def factory():
            component = TableBrowser(DatasetClient(), page_size=2)
            components.append(component)
            return component
        async with ASGIApp(App(factory), origin="https://example.test") as app:
            client = Client(app)
            self.assertEqual((await client.page()).status, 200)
            refused = await request(app, "/_spatial/close", "POST", headers=[
                (b"cookie", client.cookie), (b"origin", b"https://example.test"), (b"content-type", b"application/json")], body=b"{}")
            self.assertEqual(refused.status, 403)
            self.assertFalse(components[0].binding.closed)
            closed = await request(app, "/_spatial/close", "POST", headers=[
                (b"cookie", client.cookie), (b"x-spatial-token", client.token),
                (b"origin", b"https://example.test"), (b"content-type", b"application/json")], body=b"{}")
            self.assertEqual(closed.status, 204)
            self.assertTrue(components[0].binding.closed)
            await client.page()
        self.assertTrue(all(c.binding.closed for c in components))
        self.assertTrue(all(not thread.is_alive() for c in components for thread in c.binding._worker._threads))
