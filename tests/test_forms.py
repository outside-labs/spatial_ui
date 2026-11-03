import json
import unittest

from spatial_ui.forms import (
    Field, FieldError, FormError, FormEvent, FormResult, FormSchema, decode_event,
)


class FormTests(unittest.TestCase):
    def setUp(self):
        self.schema = FormSchema((Field("title"), Field("done", "checkbox", False, False),
                                  Field("hidden", required=False, disabled=True)))

    def test_detached_event_missing_empty_and_false_are_distinct(self):
        values = {"title": ""}
        event = self.schema.admit({"kind": "submit", "fields": values})
        values["title"] = "changed"
        self.assertEqual(dict(event.fields), {"title": "", "done": False})
        with self.assertRaises(TypeError):
            event.fields["title"] = "mutation"
        for values in ({}, {"title": "x", "done": 0}, {"title": "x", "hidden": "secret"}):
            with self.subTest(values=values), self.assertRaises(FormError):
                self.schema.admit({"kind": "submit", "fields": values})

    def test_single_field_events_and_exact_shapes(self):
        for kind in ("input", "change"):
            self.assertEqual(self.schema.admit({"kind": kind, "fields": {"done": True}}).kind, kind)
        for payload in ({"kind": "input", "fields": {}}, {"kind": "input", "fields": {"title": "x", "done": True}},
                        {"kind": "click", "fields": {}}, {"kind": "submit", "fields": [], "extra": 1}):
            with self.subTest(payload=payload), self.assertRaises(FormError):
                self.schema.admit(payload)

    def test_utf8_bounds_and_native_value_refusal(self):
        self.schema.admit({"kind": "input", "fields": {"title": "🌱" * 1024}})
        for value in ("🌱" * 1025, "\ud800", 1, None, [], {}, float("nan")):
            with self.subTest(value=repr(value)), self.assertRaises(FormError):
                self.schema.admit({"kind": "input", "fields": {"title": value}})

    def test_declaration_bounds_defaults_and_immutable_public_errors(self):
        for fields in ((), (Field("x"),) * 2, tuple(Field(f"f{i}") for i in range(65))):
            with self.assertRaises(FormError):
                FormSchema(fields)
        for kwargs in ({"name": "bad.name"}, {"name": "x", "kind": "file"},
                       {"name": "x", "disabled": True}, {"name": "x", "kind": "checkbox", "default": "false"}):
            with self.assertRaises(FormError):
                Field(**kwargs)
        errors = [FieldError("title", "required")]
        result = FormResult("invalid", errors)
        errors.clear()
        self.assertEqual(result.as_dict()["focus"], "title")
        self.assertNotIn("fields", result.as_dict())
        with self.assertRaises(FormError):
            FieldError("title", "private submitted value")

    def test_envelope_bounds_and_unique_json_keys(self):
        for value in ({"event": "opaque", "version": 1},
                      {"event": "opaque", "version": 1, "payload": {"kind": "submit", "fields": {"title": "x"}}}):
            self.assertEqual(decode_event(json.dumps(value).encode()), value)
        for body in (b'{"event":"a","version":1,"version":2}', b'{"event":"a","version":true}',
                     b'{"event":"a","version":1,"payload":{"x":1,"x":2}}',
                     b'{"event":"a","version":1,"payload":NaN}', b'\xff', b'[]', b' ' * 16385,
                     b'{"event":"a","version":9007199254740992}'):
            with self.subTest(body=body[:80]), self.assertRaises(FormError):
                decode_event(body)


if __name__ == "__main__":
    unittest.main()
