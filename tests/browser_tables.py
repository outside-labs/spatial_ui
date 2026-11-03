"""Actual Chromium acceptance for bounded read-only table interaction."""

import unittest

from playwright.sync_api import sync_playwright, expect

from spatial_ui import App, TableView
from test_table import page
from test_server import running


class BrowserTables(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.playwright = sync_playwright().start()
        cls.browser = cls.playwright.chromium.launch()

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.playwright.stop()

    def test_semantics_keyboard_escaped_values_and_independent_selection(self):
        with running(App(lambda: TableView(page(), title="Work items"))) as port:
            for width in (390, 1280):
                with self.subTest(width=width):
                    first = self.browser.new_context(viewport={"width": width, "height": 800}, reduced_motion="reduce")
                    second = self.browser.new_context()
                    try:
                        browser, other = first.new_page(), second.new_page()
                        browser.goto(f"http://127.0.0.1:{port}")
                        other.goto(f"http://127.0.0.1:{port}")
                        expect(browser.get_by_role("table", name="Work items")).to_be_visible()
                        self.assertEqual(browser.locator("thead th[scope=col]").count(), 3)
                        self.assertEqual(browser.locator("tbody th[scope=row]").count(), 2)
                        cells = browser.locator("[data-table-cell]")
                        cells.nth(0).focus()
                        cells.nth(0).press("ArrowRight")
                        expect(cells.nth(1)).to_be_focused()
                        expect(browser.locator("[data-table-view]")).to_contain_text("Selected row row-2, column done.")
                        cells.nth(1).press("ArrowDown")
                        expect(cells.nth(3)).to_be_focused()
                        expect(browser.locator("[data-table-view]")).to_contain_text("Selected row row-1, column done.")
                        cells.nth(3).press("Home")
                        expect(cells.nth(2)).to_be_focused()
                        expect(other.locator("[data-table-view]")).to_contain_text("Select a cell.")
                        self.assertEqual(browser.locator("script").count(), 1)
                        self.assertFalse(browser.evaluate("document.documentElement.scrollWidth > innerWidth"))
                        outline = cells.nth(2).evaluate("n => getComputedStyle(n).outlineStyle")
                        self.assertNotEqual(outline, "none")
                    finally:
                        first.close()
                        second.close()
