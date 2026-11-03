import unittest

from spatial_ui import Session
from spatial_ui.resource_form import ResourceForm
from spatial_ui.rest import ResourceClient, Response
from test_rest import FakeTransport, ROW, SPEC


class ResourceFormTests(unittest.TestCase):
    def setUp(self):
        self.transport = FakeTransport()
        self.form = ResourceForm(ResourceClient(self.transport, SPEC))
        self.session = Session(self.form)

    def submit(self, **values):
        snapshot = self.session.render()
        fields = {**self.form.binding.draft, "operation": "save", **values}
        return self.session.dispatch(snapshot.event_ids[0], snapshot.version, {"kind": "submit", "fields": fields})

    def test_create_validation_and_field_focus(self):
        result = self.submit(key="a", title="")
        self.assertEqual(result.form_result.as_dict()["focus"], "title")
        self.assertEqual(self.transport.calls, [])
        self.transport.response = Response(201, ROW, "1")
        result = self.submit(key="a", title="Initial")
        self.assertEqual(result.form_result.status, "saved")
        self.assertIn("readonly", result.markup)
        self.assertNotIn("never displayed", result.markup)

    def test_refresh_merge_discard_and_checked_delete(self):
        self.submit(key="a", operation="load")
        self.form.binding.edit({"title": "Local"})
        self.transport.response = Response(409, {"error": {"code": "revision_conflict"}})
        result = self.submit()
        self.assertEqual(result.form_result.status, "conflict")
        self.transport.response = Response(200, {**ROW, "title": "Remote"}, "2")
        self.submit(operation="load")
        self.assertEqual(self.form.binding.draft["title"], "Local")
        self.assertEqual(self.form.binding.revision, "2")
        self.submit(operation="discard")
        self.assertEqual(self.form.binding.draft["title"], "Remote")
        self.transport.response = Response(204)
        result = self.submit(operation="delete")
        self.assertEqual(result.form_result.status, "deleted")
        self.assertEqual(self.transport.calls[-1][2]["revision"], "2")
        self.assertEqual(self.form.binding.draft["key"], "")

    def test_new_editor_and_component_sessions_do_not_share_drafts(self):
        other = ResourceForm(self.form.binding.client)
        self.submit(key="a", operation="load")
        self.form.binding.edit({"title": "Local"})
        self.assertEqual(other.binding.draft["title"], "")
        result = self.submit(operation="new")
        self.assertEqual(result.form_result.status, "loaded")
        self.assertIsNone(self.form.binding.committed)
        self.assertEqual(self.form.binding.draft["key"], "")
