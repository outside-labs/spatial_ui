import dataclasses
import json
import unittest
from html.parser import HTMLParser

from spatial_ui import Field, FormSchema
from spatial_ui.live import LiveConfig
from spatial_ui.rest import BindingError, ResourceSpec, TransportError


class LiveConfigTests(unittest.TestCase):
    def setUp(self):
        self.spec = ResourceSpec("/notes", FormSchema((Field("key"), Field("title"),
            Field("done", "checkbox", False, False))), ("key", "title", "done"))
        self.value = {"key": "note-1", "title": "Garden 🌱", "done": False, "owner": "hidden"}

    def test_explicit_projected_declaration_has_no_write_revision(self):
        config = LiveConfig("/api/notes")
        result = json.loads(config.declaration(self.spec, self.value))
        self.assertEqual(result["path"], "/api/notes/note-1")
        self.assertEqual(result["fields"], {"key": "text", "title": "text", "done": "boolean"})
        self.assertEqual(result["baseline"], {k: self.value[k] for k in self.spec.visible})
        self.assertNotIn("revision", result)
        self.assertNotIn("hidden", config.declaration(self.spec, self.value))
        with self.assertRaises(dataclasses.FrozenInstanceError):
            config.path = "/other"

    def test_refuse_credential_and_nonstatic_routes(self):
        for path in (None, "", "//api/notes", "https://api.example/notes", "/notes?token=secret",
                     "/notes#secret", "/notes/../other", "/notes/", "/" + "a" * 513):
            with self.subTest(path=path), self.assertRaises(BindingError):
                LiveConfig(path)

    def test_refuse_unbounded_or_ambiguous_limits(self):
        for name, values in {"max_message_bytes": (True, 0, 65537), "retry_limit": (True, -1, 9),
                "retry_delay_ms": (0, 99, 5001), "handshake_timeout_ms": (99, 30001, 1.5)}.items():
            for value in values:
                with self.subTest(name=name, value=value), self.assertRaises(BindingError):
                    LiveConfig("/notes", **{name: value})

    def test_baseline_requires_the_declared_scalar_profile(self):
        for value in ({**self.value, "key": "../other"}, {**self.value, "done": 1},
                      {"key": "note-1"}, {**self.value, "title": "x" * 4097}):
            with self.subTest(value=str(value)[:50]), self.assertRaises(TransportError):
                LiveConfig("/notes").declaration(self.spec, value)


class LiveFormTests(unittest.TestCase):
    def form(self):
        from spatial_ui.resource_form import ResourceForm
        from spatial_ui.rest import ResourceClient
        from test_rest import FakeTransport, SPEC
        return ResourceForm(ResourceClient(FakeTransport(), SPEC), live=LiveConfig("/api/notes"))

    def declaration(self, form):
        from spatial_ui import Session
        declarations = []
        class Parser(HTMLParser):
            def handle_starttag(self, tag, attrs):
                for name, value in attrs:
                    if name == "data-sui-live":
                        declarations.append(json.loads(value))
        Parser().feed(Session(form).render().markup)
        return declarations

    def test_opt_in_only_subscribes_loaded_record_and_removes_on_new_or_close(self):
        form = self.form()
        self.assertEqual(self.declaration(form), [])
        form.binding.perform("load", key="a")
        self.assertEqual(self.declaration(form)[0]["path"], "/api/notes/a")
        form.binding.discard(new=True)
        self.assertEqual(self.declaration(form), [])
        form.binding.perform("load", key="a")
        form.binding.close()
        self.assertEqual(self.declaration(form), [])

    def test_live_declaration_preserves_draft_revision_and_visibility(self):
        form = self.form()
        form.binding.perform("load", key="a")
        form.binding.edit({"title": '<draft>"🌱'})
        declaration = self.declaration(form)[0]
        self.assertNotIn("never displayed", json.dumps(declaration))
        self.assertEqual(declaration["baseline"]["title"], "Initial")
        self.assertEqual(form.binding.draft["title"], '<draft>"🌱')
        self.assertEqual(form.binding.revision, "1")
        self.assertNotIn("revision", declaration)
        self.assertEqual(self.declaration(self.form()), [])

    def test_refuse_implicit_live_configuration(self):
        from spatial_ui.resource_form import ResourceForm
        with self.assertRaises(BindingError):
            ResourceForm(self.form().binding.client, live="/api/notes")


if __name__ == "__main__":
    unittest.main()
