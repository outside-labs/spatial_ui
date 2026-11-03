"""Actual Chromium acceptance for bounded read-only table interaction."""

import unittest
import threading
import time

from playwright.sync_api import sync_playwright, expect

from spatial_ui import App, TableBrowser, TableView
from test_table import page
from test_table_binding import DatasetClient
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

    def test_dataset_sort_search_columns_composition_late_results_and_teardown(self):
        client = DatasetClient()
        initial = list(client.data)
        components = []
        entered, release = threading.Event(), threading.Event()
        original = client.query
        def query(descriptor, cancel):
            if descriptor["search"] and descriptor["search"]["term"] == "hold":
                entered.set()
                if not release.wait(5):
                    raise TimeoutError
            return original(descriptor, cancel)
        client.query = query
        def factory():
            component = TableBrowser(client, page_size=2)
            components.append(component)
            return component
        with running(App(factory)) as port:
            context = self.browser.new_context(viewport={"width": 390, "height": 800}, reduced_motion="reduce")
            other_context = self.browser.new_context()
            try:
                browser, other = context.new_page(), other_context.new_page()
                browser.goto(f"http://127.0.0.1:{port}")
                other.goto(f"http://127.0.0.1:{port}")
                cells = browser.locator("[data-table-cell]")
                expect(cells.first).to_have_attribute("data-row-key", "row-0")
                browser.get_by_role("button", name="Next page", exact=True).click()
                expect(cells.first).to_have_attribute("data-row-key", "row-2")
                browser.get_by_role("button", name="Sort Title: none", exact=True).click()
                expect(cells.first).to_have_attribute("data-row-key", "row-0")
                browser.get_by_role("button", name="Sort Title: ascending", exact=True).click()
                expect(cells.first).to_have_attribute("data-row-key", "row-7")
                browser.get_by_role("button", name="Show Rank", exact=True).click()
                expect(browser.locator("thead th")).to_have_count(2)
                browser.get_by_role("button", name="Show Rank", exact=True).click()
                browser.get_by_role("button", name="Move rank earlier", exact=True).click()
                expect(browser.locator("thead th").nth(1)).to_have_text("Sort Rank: none")
                search = browser.get_by_label("Search table", exact=True)
                search.evaluate('''n => { n.dispatchEvent(new CompositionEvent("compositionstart",{bubbles:true}));
                    n.value="Title 5"; n.dispatchEvent(new InputEvent("input",{bubbles:true,isComposing:true}));
                    n.dispatchEvent(new CompositionEvent("compositionend",{bubbles:true})); }''')
                expect(cells.first).to_have_attribute("data-row-key", "row-5")
                expect(search).to_have_value("Title 5")
                search.fill("hold")
                self.assertTrue(entered.wait(3))
                search.fill("Title 6")
                deadline = time.monotonic() + 3
                while components[0].binding.term != "Title 6" and time.monotonic() < deadline:
                    browser.wait_for_timeout(10)
                self.assertEqual(components[0].binding.term, "Title 6")
                release.set()
                expect(cells.first).to_have_attribute("data-row-key", "row-6")
                expect(other.locator("[data-table-cell]").first).to_have_attribute("data-row-key", "row-0")
                self.assertEqual(client.data, initial)
                self.assertFalse(browser.evaluate("document.documentElement.scrollWidth > innerWidth"))
                browser.locator("#sui-app").dispatch_event("sui:stop")
                deadline = time.monotonic() + 3
                while not components[0].binding.closed and time.monotonic() < deadline:
                    browser.wait_for_timeout(10)
                self.assertTrue(components[0].binding.closed)
            finally:
                release.set()
                context.close()
                other_context.close()
        self.assertTrue(all(c.binding.closed for c in components))
