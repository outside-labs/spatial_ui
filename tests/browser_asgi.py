"""Run the actual form/browser contract through the native ASGI adapter."""

import json
from pathlib import Path

import browser_forms
from asgi_test_host import running_asgi
from playwright.sync_api import expect
from spatial_ui import App
from test_form_sessions import Editor


class BrowserASGIForms(browser_forms.BrowserForms):
    def running(self, app):
        return running_asgi(app)

    def test_stale_binding_and_late_response_refusal_after_stop(self):
        with self.running(App(Editor)) as port:
            context = self.browser.new_context()
            try:
                page = context.new_page()
                page.goto(f"http://127.0.0.1:{port}")
                page.locator("#title").fill("Accepted save")
                old = page.evaluate('''() => ({
                  event: document.querySelector('form').dataset.suiForm,
                  version: Number(document.querySelector('#sui-app').dataset.version),
                  payload: {kind: 'submit', fields: {title: 'Replay', done: false}}
                })''')
                page.locator("#title").press("Enter")
                expect(page.locator("#saves")).to_have_text("Saves: 1")
                stale = page.evaluate('''async payload => (await fetch('/_spatial/event', {
                  method: 'POST', headers: {'Content-Type': 'application/json',
                    'X-Spatial-Token': document.querySelector('meta[name=sui-token]').content},
                  body: JSON.stringify(payload)
                })).status''', old)
                self.assertEqual(stale, 409)
                page.evaluate('''() => {
                  const original = window.fetch;
                  window.fetch = async (...args) => {
                    const response = await original(...args);
                    if (JSON.parse(args[1].body).payload?.kind === 'submit') {
                      await new Promise(resolve => { window.releaseSave = resolve; });
                    }
                    return response;
                  };
                }''')
                page.get_by_role("button", name="Save", exact=True).click()
                page.wait_for_function("() => typeof window.releaseSave === 'function'")
                version = page.locator("#sui-app").get_attribute("data-version")
                page.locator("#sui-app").dispatch_event("sui:stop")
                page.evaluate("() => window.releaseSave()")
                expect(page.locator("#sui-status")).to_contain_text("Reload to reconcile")
                self.assertEqual(page.locator("#sui-app").get_attribute("data-version"), version)
                page.reload()
                expect(page.locator("#saves")).to_have_text("Saves: 2")
            finally:
                context.close()

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        Path("asgi-browser-receipt.json").write_text(json.dumps({
            "profile": "spatial-asgi/1", "browser": "Chromium", "widths": [390, 1280],
            "independent_contexts": 2, "reduced_motion": True,
            "cases": ["typed-input", "keyboard-submit", "checkbox", "validation-focus", "session-isolation",
                      "draft-preservation", "composition", "duplicate-submit", "stale-binding", "stop-late-response"],
            "shutdown": {"sessions": 0, "requests": 0, "worker_closed": True},
        }, indent=2) + "\n", encoding="utf-8")
