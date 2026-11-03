import json
import re
import unittest

from spatial_ui import (
    App, CallbackError, Component, EventError, Field, FieldError, FormBinding,
    FormResult, FormSchema, Session, TemplateError, html,
)
from test_server import Client, running


class Editor(Component):
    schema = FormSchema((Field("title"), Field("done", "checkbox")))

    def __init__(self):
        self.title, self.done, self.saves = "", False, 0

    def handle(self, event):
        if event.kind != "submit":
            return FormResult()
        if not event.fields["title"].strip():
            return FormResult("invalid", (FieldError("title", "required"),))
        self.title, self.done = event.fields["title"], event.fields["done"]
        self.saves += 1
        return FormResult("saved")

    def render(self):
        binding = FormBinding(self.schema, self.handle)
        return html(t'''<main><form key="editor" on_form={binding} novalidate>
          <label for="title">Title</label><input key="title" id="title" name="title" value={self.title}>
          <label for="done">Done</label><input key="done" id="done" name="done" type="checkbox" checked={self.done}>
          <button type="submit">Save</button></form><p id="saves">Saves: {self.saves}</p></main>''')


def event(page, fields, kind="submit"):
    return {"event": re.search(r'data-sui-form="([^"]+)"', page)[1],
            "version": int(re.search(r'data-version="(\d+)"', page)[1]),
            "payload": {"kind": kind, "fields": fields}}


class FormSessionTests(unittest.TestCase):
    def test_http_validation_replay_and_independent_sessions(self):
        with running(App(Editor)) as port:
            first, second = Client(port), Client(port)
            a, b = first.page()[2], second.page()[2]
            invalid = event(a, {"title": "", "done": False})
            status, _, body = first.post(invalid)
            result = json.loads(body)
            self.assertEqual(status, 200)
            self.assertEqual(result["form"]["focus"], "title")
            self.assertIn("Saves: 0", result["markup"])
            self.assertEqual(first.post(invalid)[0], 409)
            fresh = first.page()[2]
            valid = event(fresh, {"title": "Garden 🌱", "done": True})
            self.assertEqual(second.post({**valid, "version": event(b, {})["version"]})[0], 404)
            status, _, body = first.post(valid)
            self.assertEqual(status, 200)
            self.assertIn("Saves: 1", json.loads(body)["markup"])
            self.assertIn("Saves: 0", second.page()[2])

    def test_http_rejects_malformed_input_before_callback(self):
        with running(App(Editor)) as port:
            client = Client(port)
            page = client.page()[2]
            for fields in ({"title": "x"}, {"title": "x", "done": 1}, {"private": "x"}):
                self.assertEqual(client.post(event(page, fields))[0], 400)
            payload = json.dumps(event(page, {"title": "x", "done": False}))
            duplicate = payload.replace('"done": false', '"done": false, "done": true')
            self.assertEqual(client.request("POST", "/_spatial/event", duplicate)[0], 400)
            self.assertEqual(client.post(event(page, {"title": "x", "done": False}))[0], 200)

    def test_failure_and_bad_result_invalidate_bindings(self):
        for callback in (lambda event: None, lambda event: FormResult("invalid", (FieldError("private"),))):
            editor = Editor()
            editor.handle = callback
            session = Session(editor)
            snapshot = session.render()
            with self.assertRaises(CallbackError):
                session.dispatch(snapshot.event_ids[0], snapshot.version,
                                 {"kind": "submit", "fields": {"title": "x", "done": False}})

    def test_form_callbacks_require_one_synchronous_argument(self):
        async def asynchronous(event):
            return FormResult()
        for callback in (lambda: None, asynchronous):
            editor = Editor()
            editor.handle = callback
            with self.assertRaises(EventError):
                Session(editor).render()

    def test_control_declarations_are_enforced(self):
        class Bad(Editor):
            def render(self):
                binding = FormBinding(self.schema, self.handle)
                return html(t'<form key="editor" on_form={binding}><input name="private"></form>')
        with self.assertRaises(TemplateError):
            Session(Bad()).render()
