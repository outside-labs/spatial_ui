import copy
import threading
import time
import unittest

from spatial_ui import HTTPTableClient, Session, TableBinding, TableBrowser, TableError
from spatial_ui.rest import Response


class EditableTransport:
    """Finite wire fixture shared by independent sessions and workers."""

    def __init__(self):
        self.columns = [dict(id=name, label=name.title(), type=kind, nullable=nullable, visible=True,
                             sortable=True, searchable=kind == "text", writable=name != "key")
                        for name, kind, nullable in [("key", "text", False), ("title", "text", False),
                            ("done", "boolean", False), ("rank", "integer", True), ("score", "number", False)]]
        self.rows = {"a": dict(key="a", revision="1", values=dict(key="a", title="Alpha", done=False, rank=1, score=1.5)),
                     "b": dict(key="b", revision="1", values=dict(key="b", title="Zulu", done=True, rank=None, score=2.5))}
        self.lock = threading.Lock()
        self.writes = []
        self.refusal, self.lose = None, False
        self.committed = self.release = None

    def client(self):
        return HTTPTableClient(self, "/rows", dict(contract="tabular/1", table_id="items", schema_version="1", columns=self.columns), key_column="key")

    def request(self, method, path, *, data=None):
        with self.lock:
            if path.endswith("/cell"):
                self.writes.append(copy.deepcopy(data))
                if self.refusal:
                    return Response(422, {"error": {"code": self.refusal}})
                row = self.rows[data["key"]]
                if row["revision"] != data["expected_revision"]:
                    return Response(409, {"error": {"code": "revision_conflict"}})
                row["values"][data["column_id"]] = data["value"]
                row["revision"] = str(int(row["revision"]) + 1)
                result = copy.deepcopy(row)
            else:
                rows = copy.deepcopy(list(self.rows.values()))
                for predicate in data.get("filters", []):
                    rows = [r for r in rows if r["values"][predicate["column_id"]] == predicate["value"]]
                if data.get("search"):
                    rows = [r for r in rows if data["search"]["term"] in r["values"]["title"]]
                for item in reversed(data.get("order", [])):
                    rows.sort(key=lambda r: str(r["values"][item["column_id"]]), reverse=item["descending"])
                selected = data["columns"]
                return Response(200, dict(contract="tabular/1", table_id="items", schema_version="1", generation=data["generation"],
                    columns=selected, rows=[dict(key=r["key"], revision=r["revision"], values={n: r["values"][n] for n in selected}) for r in rows[:data["limit"]]], next_cursor=None))
        if self.committed:
            self.committed.set()
            if not self.release.wait(5):
                raise TimeoutError
        if self.lose:
            raise ConnectionError("private lost response")
        return Response(200, dict(contract="tabular/1", table_id="items", schema_version="1", row=result))


def settled(binding):
    deadline = time.monotonic() + 5
    while binding.status == "loading" or binding._write is not None:
        binding.poll()
        if time.monotonic() >= deadline:
            raise AssertionError("table operation did not settle")
        time.sleep(.001)


class CellEditTests(unittest.TestCase):
    def binding(self, transport=None):
        transport = transport or EditableTransport()
        binding = TableBinding(transport.client(), editable=True)
        self.addCleanup(binding.close)
        settled(binding)
        return binding, transport

    def test_explicit_mode_and_identity_survive_view_changes(self):
        binding, transport = self.binding()
        binding.begin_edit("a", "title")
        binding.stage("Saved")
        binding.sort("title"); settled(binding)
        binding.search("Zulu"); settled(binding)
        binding.project(["rank", "title"]); settled(binding)
        self.assertEqual(binding.draft.key, "a")
        binding.save(); settled(binding)
        self.assertEqual(binding.edit_status, "saved")
        self.assertEqual(transport.writes[-1]["key"], "a")
        self.assertEqual(transport.rows["a"]["values"]["title"], "Saved")
        self.assertEqual(transport.rows["b"]["revision"], "1")
        self.assertIsNone(binding.draft)
        readonly = TableBinding(transport.client())
        self.addCleanup(readonly.close)
        settled(readonly)
        readonly.begin_edit("a", "title")
        self.assertIsNone(readonly.draft)

    def test_two_sessions_conflict_preserves_draft_and_explicit_review(self):
        first, transport = self.binding()
        second, _ = self.binding(transport)
        for binding in (first, second):
            binding.begin_edit("a", "title")
        first.stage("First"); first.save(); settled(first)
        second.stage("Second"); second.save(); settled(second)
        self.assertEqual((second.edit_status, second.draft.text, second.draft.revision), ("conflict", "Second", "1"))
        second.save(); self.assertEqual(len(transport.writes), 2)
        second.reload_draft(); settled(second)
        self.assertEqual(second.edit_status, "review")
        self.assertEqual(second.draft.current.values[1], "First")
        second.reapply()
        self.assertEqual((second.draft.text, second.draft.revision), ("Second", "2"))
        second.save(); settled(second)
        self.assertEqual(transport.rows["a"]["revision"], "3")

    def test_scalar_editors_null_invalid_and_read_only(self):
        binding, transport = self.binding()
        binding.begin_edit("a", "key")
        self.assertEqual(binding.edit_status, "read_only")
        for column, text, expected in [("done", "true", True), ("rank", "12", 12), ("score", "2.75", 2.75)]:
            binding.begin_edit("a", column)
            binding.stage("invalid"); binding.save()
            self.assertEqual(binding.edit_status, "invalid")
            binding.stage(text); binding.save(); settled(binding)
            self.assertEqual(transport.rows["a"]["values"][column], expected)
        binding.begin_edit("a", "rank")
        binding.stage(is_null=True); binding.save(); settled(binding)
        self.assertIsNone(transport.rows["a"]["values"]["rank"])
        binding.begin_edit("a", "title")
        binding.stage(is_null=True); binding.save()
        self.assertEqual(binding.edit_status, "invalid")
        binding.discard()
        for code, status in [("validation", "invalid"), ("read_only", "read_only"), ("forbidden", "denied")]:
            binding.begin_edit("a", "title")
            binding.stage("Kept")
            transport.refusal = code
            binding.save(); settled(binding)
            self.assertEqual((binding.edit_status, binding.draft.text), (status, "Kept"))
            binding.discard()

    def test_lost_response_requires_reload_before_retry(self):
        binding, transport = self.binding()
        binding.begin_edit("a", "title"); binding.stage("Committed")
        transport.lose = True
        binding.save(); settled(binding)
        self.assertEqual((binding.edit_status, binding.draft.text), ("uncertain", "Committed"))
        binding.save(); self.assertEqual(len(transport.writes), 1)
        transport.lose = False
        binding.reload_draft(); settled(binding)
        self.assertEqual(binding.draft.current.revision, "2")
        binding.discard()
        self.assertIsNone(binding.draft)

    def test_late_write_query_and_source_changes_refuse_completion(self):
        for replace_source in (False, True):
            binding, transport = self.binding()
            transport.committed, transport.release = threading.Event(), threading.Event()
            binding.begin_edit("a", "title"); binding.stage("Committed")
            binding.save()
            self.assertTrue(transport.committed.wait(2))
            if replace_source:
                binding.replace(EditableTransport().client())
            else:
                binding.search("Zulu")
            transport.release.set(); settled(binding)
            self.assertEqual(binding.edit_status, "uncertain")
            self.assertEqual(binding.draft.text, "Committed")
            if not replace_source:
                binding.reload_draft(); settled(binding)
                self.assertEqual(binding.draft.current.revision, "2")
            else:
                binding.reload_draft()
                self.assertIsNone(binding._write)

    def test_component_markup_draft_identity_and_cleanup(self):
        component = TableBrowser(EditableTransport().client(), editable=True)
        session = Session(component)
        self.addCleanup(session.close)
        settled(component.binding)
        component.binding.begin_edit("a", "title")
        component.binding.stage("<script>draft</script>")
        markup = session.render().markup
        self.assertIn("Editing row a", markup)
        self.assertIn("data-table-editor", markup)
        self.assertNotIn("<script>draft", markup)
        session.close()
        self.assertTrue(component.binding.closed)
        self.assertTrue(all(not t.is_alive() for t in component.binding._worker._threads))

    def test_pending_editor_declares_disabled_controls(self):
        transport = EditableTransport()
        transport.committed, transport.release = threading.Event(), threading.Event()
        component = TableBrowser(transport.client(), editable=True)
        session = Session(component)
        try:
            settled(component.binding)
            component.binding.begin_edit("a", "done")
            component.binding.stage("true")
            component.binding.save()
            self.assertTrue(transport.committed.wait(2))
            markup = session.render().markup
            self.assertIn("Saving cell.", markup)
            self.assertIn("<select", markup)
        finally:
            transport.release.set()
            session.close()
