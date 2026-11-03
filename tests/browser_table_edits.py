"""Actual Chromium checks for explicit scalar editing and draft recovery."""

import threading
import time
import unittest

from playwright.sync_api import expect, sync_playwright

from spatial_ui import App, TableBrowser
from test_server import running
from test_table_edits import EditableTransport


class BrowserCellEdits(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.playwright = sync_playwright().start()
        cls.browser = cls.playwright.chromium.launch()

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.playwright.stop()

    def begin(self, page, key="a", column="title"):
        page.locator(f'[data-table-cell][data-row-key="{key}"][data-column-id="{column}"]').click()
        page.get_by_role("button", name="Edit selected cell", exact=True).click()
        expect(page.get_by_label("Cell value", exact=True)).to_be_visible()

    def state(self, page, value):
        expect(page.locator("[data-table-edit-state]")).to_have_attribute("data-table-edit-state", value)

    def test_two_browsers_conflicts_composition_focus_view_changes_and_escape(self):
        transport, components = EditableTransport(), []
        def factory():
            component = TableBrowser(transport.client(), editable=True, title="Editable items")
            components.append(component)
            return component
        with running(App(factory)) as port:
            contexts = [self.browser.new_context(viewport={"width": width, "height": 850}, reduced_motion="reduce") for width in (390, 1280)]
            try:
                first, second = [context.new_page() for context in contexts]
                for page in (first, second):
                    page.goto(f"http://127.0.0.1:{port}")
                    self.begin(page)
                value = first.get_by_label("Cell value", exact=True)
                expect(value).to_be_focused()
                value.evaluate('''n => { n.dispatchEvent(new CompositionEvent("compositionstart",{bubbles:true}));
                    n.value="Composed café"; n.dispatchEvent(new InputEvent("input",{bubbles:true,isComposing:true}));
                    n.dispatchEvent(new CompositionEvent("compositionend",{bubbles:true})); }''')
                expect(value).to_have_value("Composed café")
                first.get_by_role("button", name="Sort Title: none", exact=True).click()
                first.get_by_label("Search table", exact=True).fill("Zulu")
                expect(first.locator("[data-table-cell]").first).to_have_attribute("data-row-key", "b")
                expect(value).to_have_value("Composed café")
                expect(first.locator("form[data-table-editor]")).to_contain_text("Editing row a")
                value.press("Enter")
                self.state(first, "saved")
                self.assertEqual(transport.rows["a"]["values"]["title"], "Composed café")
                draft = second.get_by_label("Cell value", exact=True)
                draft.fill("Other draft")
                second.get_by_role("button", name="Save cell", exact=True).click()
                self.state(second, "conflict")
                expect(draft).to_have_value("Other draft")
                expect(second.get_by_role("button", name="Save cell", exact=True)).to_be_disabled()
                second.get_by_role("button", name="Reload current row", exact=True).click()
                self.state(second, "review")
                expect(second.locator("form[data-table-editor]")).to_contain_text("Current value: Composed café")
                second.get_by_role("button", name="Reapply draft to current revision", exact=True).click()
                second.get_by_role("button", name="Save cell", exact=True).click()
                self.state(second, "saved")
                self.assertEqual(transport.rows["a"]["revision"], "3")
                self.begin(second, column="rank")
                number = second.get_by_label("Cell value", exact=True)
                number.fill("invalid rank")
                number.press("Enter")
                self.state(second, "invalid")
                expect(number).to_have_value("invalid rank")
                expect(number).to_be_focused()
                number.press("Escape")
                self.state(second, "idle")
                expect(second.locator('[data-table-cell][data-row-key="a"][data-column-id="rank"]')).to_be_focused()
                self.assertFalse(first.evaluate("document.documentElement.scrollWidth > innerWidth"))
                self.assertFalse(second.evaluate("document.documentElement.scrollWidth > innerWidth"))
                for page in (first, second):
                    page.locator("#sui-app").dispatch_event("sui:stop")
                deadline = time.monotonic() + 3
                while not all(c.binding.closed for c in components) and time.monotonic() < deadline:
                    time.sleep(.01)
                self.assertTrue(all(c.binding.closed for c in components))
            finally:
                for context in contexts:
                    context.close()

    def test_lost_response_reloads_before_explicit_reapply(self):
        transport = EditableTransport()
        with running(App(lambda: TableBrowser(transport.client(), editable=True))) as port:
            context = self.browser.new_context()
            try:
                page = context.new_page()
                page.goto(f"http://127.0.0.1:{port}")
                self.begin(page)
                page.get_by_label("Cell value", exact=True).fill("Committed once")
                transport.lose = True
                page.get_by_role("button", name="Save cell", exact=True).click()
                self.state(page, "uncertain")
                expect(page.get_by_label("Cell value", exact=True)).to_have_value("Committed once")
                expect(page.get_by_role("button", name="Save cell", exact=True)).to_be_disabled()
                transport.lose = False
                page.get_by_role("button", name="Reload current row", exact=True).click()
                self.state(page, "review")
                expect(page.locator("form[data-table-editor]")).to_contain_text("Current revision: 2")
                page.get_by_role("button", name="Discard cell draft", exact=True).click()
                self.state(page, "idle")
                self.assertEqual(len(transport.writes), 1)
            finally:
                context.close()

    def test_scalar_controls_canceled_committed_save_and_fresh_read(self):
        transport = EditableTransport()
        with running(App(lambda: TableBrowser(transport.client(), editable=True))) as port:
            context = self.browser.new_context()
            try:
                page = context.new_page()
                page.goto(f"http://127.0.0.1:{port}")
                self.begin(page, column="done")
                page.get_by_label("Cell value", exact=True).select_option("true")
                page.get_by_role("button", name="Save cell", exact=True).click()
                self.state(page, "saved")
                self.assertIs(transport.rows["a"]["values"]["done"], True)
                self.begin(page, column="rank")
                page.get_by_label("Use null", exact=True).check()
                page.get_by_role("button", name="Save cell", exact=True).click()
                self.state(page, "saved")
                self.assertIsNone(transport.rows["a"]["values"]["rank"])
                self.begin(page, column="score")
                page.get_by_label("Cell value", exact=True).fill("12.25")
                page.get_by_role("button", name="Save cell", exact=True).click()
                self.state(page, "saved")
                self.assertEqual(transport.rows["a"]["values"]["score"], 12.25)
                self.begin(page)
                page.get_by_label("Cell value", exact=True).fill("Canceled after commit")
                transport.committed, transport.release = threading.Event(), threading.Event()
                page.get_by_role("button", name="Save cell", exact=True).click()
                self.assertTrue(transport.committed.wait(2))
                self.state(page, "pending")
                page.get_by_role("button", name="Refresh table", exact=True).click()
                transport.release.set()
                self.state(page, "uncertain")
                expect(page.get_by_label("Cell value", exact=True)).to_have_value("Canceled after commit")
                page.get_by_role("button", name="Reload current row", exact=True).click()
                self.state(page, "review")
                expect(page.locator("form[data-table-editor]")).to_contain_text("Current value: Canceled after commit")
                self.assertEqual(len(transport.writes), 4)
            finally:
                if transport.release:
                    transport.release.set()
                context.close()

    def test_oversized_unicode_draft_stays_local_and_can_be_corrected_or_discarded(self):
        transport = EditableTransport()
        with running(App(lambda: TableBrowser(transport.client(), editable=True))) as port:
            context = self.browser.new_context()
            try:
                page = context.new_page()
                page.goto(f"http://127.0.0.1:{port}")
                self.begin(page)
                value = page.get_by_label("Cell value", exact=True)
                oversized = "é" * 2100
                value.fill(oversized)
                expect(value).to_have_attribute("aria-invalid", "true")
                expect(page.locator("#sui-status")).to_contain_text("exceeds 4,096 UTF-8 bytes")
                expect(value).to_be_focused()
                self.assertFalse(value.evaluate("n => n.checkValidity()"))
                value.press("Enter")
                self.assertEqual(transport.writes, [])
                page.get_by_label("Search table", exact=True).fill("Zulu")
                expect(page.locator("[data-table-cell]").first).to_have_attribute("data-row-key", "b")
                expect(value).to_have_value(oversized)
                expect(value).to_have_attribute("aria-invalid", "true")
                value.fill("Corrected café")
                value.press("Enter")
                self.state(page, "saved")
                self.assertEqual(transport.rows["a"]["values"]["title"], "Corrected café")
                page.get_by_label("Search table", exact=True).fill("")
                self.begin(page)
                value.fill(oversized)
                expect(value).to_have_attribute("aria-invalid", "true")
                value.press("Escape")
                self.state(page, "idle")
                self.assertEqual(len(transport.writes), 1)
            finally:
                context.close()
