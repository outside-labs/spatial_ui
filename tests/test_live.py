import dataclasses
import json
import unittest

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


if __name__ == "__main__":
    unittest.main()
