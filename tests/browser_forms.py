"""Actual Chromium acceptance; run explicitly with the browser-tests group."""

import unittest

from playwright.sync_api import sync_playwright, expect

from spatial_ui import App
from test_form_sessions import Editor
from test_server import running


class BrowserForms(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.playwright = sync_playwright().start()
        cls.browser = cls.playwright.chromium.launch()

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.playwright.stop()

    def test_typing_keyboard_validation_checkbox_and_session_isolation(self):
        with running(App(Editor)) as port:
            for width in (390, 1280):
                with self.subTest(width=width):
                    first = self.browser.new_context(viewport={"width": width, "height": 800}, reduced_motion="reduce")
                    second = self.browser.new_context()
                    try:
                        page, other = first.new_page(), second.new_page()
                        errors = []
                        page.on("pageerror", lambda error: errors.append(str(error)))
                        page.goto(f"http://127.0.0.1:{port}")
                        other.goto(f"http://127.0.0.1:{port}")
                        page.get_by_role("button", name="Save", exact=True).click()
                        expect(page.locator("#title")).to_be_focused()
                        expect(page.locator("#title")).to_have_attribute("aria-invalid", "true")
                        page.locator("#title").press_sequentially("Garden 🌱")
                        page.locator("#done").check()
                        page.locator("#title").press("Enter")
                        expect(page.locator("#saves")).to_have_text("Saves: 1")
                        expect(page.locator("#title")).to_have_value("Garden 🌱")
                        expect(page.locator("#done")).to_be_checked()
                        expect(other.locator("#saves")).to_have_text("Saves: 0")
                        expect(other.locator("#title")).to_have_value("")
                        self.assertFalse(page.evaluate("document.documentElement.scrollWidth > innerWidth"))
                        self.assertEqual(errors, [])
                    finally:
                        first.close()
                        second.close()

    def test_later_typing_duplicate_submit_composition_and_stop(self):
        with running(App(Editor)) as port:
            context = self.browser.new_context()
            try:
                page = context.new_page()
                page.goto(f"http://127.0.0.1:{port}")
                page.locator("#title").fill("Original")
                expect(page.locator("#sui-app")).not_to_have_attribute("aria-busy", "true")
                # Delay a real completed HTTP save while the user continues typing.
                page.evaluate('''() => {
                  const original = window.fetch;
                  window.fetch = async (...args) => {
                    const response = await original(...args);
                    if (JSON.parse(args[1].body).payload?.kind === "submit") {
                      await new Promise(resolve => { window.releaseSave = resolve; });
                    }
                    return response;
                  };
                }''')
                page.get_by_role("button", name="Save", exact=True).click()
                page.wait_for_function("() => typeof window.releaseSave === 'function'")
                page.get_by_role("button", name="Save", exact=True).click()
                page.locator("#title").fill("Later draft")
                page.locator("#title").evaluate("node => node.setSelectionRange(2, 5)")
                page.evaluate("window.releaseSave()")
                expect(page.locator("#saves")).to_have_text("Saves: 1")
                expect(page.locator("#title")).to_have_value("Later draft")
                expect(page.locator("#title")).to_be_focused()
                self.assertEqual(page.locator("#title").evaluate("node => [node.selectionStart, node.selectionEnd]"), [2, 5])
                page.locator("#title").evaluate('''node => {
                  node.dispatchEvent(new CompositionEvent("compositionstart", {bubbles:true}));
                  node.value = "日本語";
                  node.dispatchEvent(new InputEvent("input", {bubbles:true,isComposing:true}));
                  node.dispatchEvent(new CompositionEvent("compositionend", {bubbles:true}));
                  node.dispatchEvent(new InputEvent("input", {bubbles:true}));
                }''')
                expect(page.locator("#title")).to_have_value("日本語")
                page.locator("#sui-app").dispatch_event("sui:stop")
                version = page.locator("#sui-app").get_attribute("data-version")
                page.locator("form").dispatch_event("submit")
                expect(page.locator("#sui-status")).to_contain_text("Reload to reconcile")
                self.assertEqual(page.locator("#sui-app").get_attribute("data-version"), version)
            finally:
                context.close()
