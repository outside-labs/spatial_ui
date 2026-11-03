from dataclasses import FrozenInstanceError
from types import SimpleNamespace
import unittest

from spatial_ui import Session, TableColumn, TableError, TablePage, TableRow, TableView


def page(table_id="items", rows=None):
    return TablePage(table_id, "1", (TableColumn("title", "Same label", True, True), TableColumn("done", "Same label")),
                     (TableRow("row-2", ("<script>alert(1)</script>", "False"), "2"), TableRow("row-1", ("duplicate", "False"), "1")) if rows is None else rows)


class TableViewTests(unittest.TestCase):
    def test_semantic_escaped_static_page_selection_and_independent_sessions(self):
        first, second = TableView(page()), TableView(page())
        session = Session(first)
        snapshot = session.render()
        self.assertIn("&lt;script&gt;", snapshot.markup)
        self.assertNotIn("<script>", snapshot.markup)
        self.assertIn('<caption>Table</caption>', snapshot.markup)
        self.assertIn('scope="row"', snapshot.markup)
        self.assertIn('data-row-key="row-2"', snapshot.markup)
        self.assertIn(":focus-visible", snapshot.css)
        self.assertEqual(len(snapshot.event_ids), 4)
        session.dispatch(snapshot.event_ids[0], snapshot.version)
        self.assertEqual(first.selected, ("row-2", "title"))
        self.assertIsNone(second.selected)
        self.assertEqual(first.page.rows[0].revision, "2")
        with self.assertRaises(FrozenInstanceError):
            first.page.rows[0].revision = "3"

    def test_source_snapshot_adapter_has_no_dependency_import(self):
        source = SimpleNamespace(table_id="memory-1", schema_version="1", generation=5, next_cursor=None,
            columns=[SimpleNamespace(id="c1", label="Text", sortable=True, searchable=True)],
            rows=[SimpleNamespace(key="r1", revision=None, cells=[SimpleNamespace(value="=opaque")])])
        captured = TablePage.from_source(source)
        self.assertEqual(captured.rows[0].values, ("=opaque",))
        self.assertEqual(captured.generation, 5)
        source.rows[0].cells[0].value = "changed"
        self.assertEqual(captured.rows[0].values, ("=opaque",))

    def test_states_replacement_empty_and_refusals(self):
        component = TableView(page(), status="loading")
        self.assertIn("Loading table.", Session(component).render().markup)
        component.status = "error"
        self.assertIn("could not be loaded", Session(component).render().markup)
        component.select("row-1", "done")
        component.replace(page("other", rows=()))
        self.assertIsNone(component.selected)
        component.status = "ready"
        self.assertIn("No rows match", Session(component).render().markup)
        with self.assertRaises(TableError):
            component.select("row-1", "title")
        for invalid in [lambda: TableRow("row", ("x" * 16385,)),
                        lambda: TablePage("t", "1", (TableColumn("a", "A"),), (TableRow("r", ("a", "b")),)),
                        lambda: TablePage("t", "1", (TableColumn("a", "A"),) * 65, ()),
                        lambda: TablePage("t", "1", (), (), generation=True)]:
            with self.assertRaises(TableError):
                invalid()
