import unittest

from spatial_ui import (
    BoundsError, Breakpoint, Grid, TemplateError, at, below, html, render,
)


class TemplateTests(unittest.TestCase):
    def test_text_attribute_and_entity_escaping(self):
        attack = '<script>alert("x")</script> & " onclick="bad'
        result = render(html(t'<p title={attack}>{attack} &amp; done</p>'))
        self.assertNotIn('<script>', result.markup)
        self.assertIn('title="&lt;script&gt;alert(&quot;x&quot;)', result.markup)
        self.assertIn('&lt;script&gt;alert("x")&lt;/script&gt;', result.markup)
        self.assertTrue(result.markup.endswith('&amp; done</p>'))

    def test_nested_templates_sequences_and_omitted_attributes(self):
        children = [html(t'<strong>Hello</strong>'), t'<span>{3}</span>', None, '<b>text</b>']
        result = render(html(t'<div title={None} data-value="count-{2}">{children}</div>'))
        self.assertEqual(result.markup, '<div data-value="count-2"><strong>Hello</strong><span>3</span>&lt;b&gt;text&lt;/b&gt;</div>')
        self.assertEqual(render(html(t'<input disabled={False} required={True} value={"&"}>')).markup,
                         '<input required value="&amp;">')

    def test_no_implicit_raw_markup_or_formatting(self):
        with self.assertRaises(TypeError):
            html('<b>hello</b>')
        for template in (t'<p>{object()}</p>', t'<p>{1!r}</p>', t'<p>{1:.2f}</p>'):
            with self.subTest(template=template), self.assertRaises(TemplateError):
                html(template)
        sequence = []
        sequence.append(sequence)
        with self.assertRaises(TemplateError):
            html(t'<p>{sequence}</p>')

    def test_urls_reject_script_obfuscation_and_unsupported_schemes(self):
        attacks = ['javascript:alert(1)', 'JaVaScRiPt:alert(1)', 'java\nscript:alert(1)',
                   '\x00javascript:bad', ' javascript:bad', '//example.com', '\\example.com',
                   'data:text/html,<script>bad</script>', 'file:///etc/passwd']
        for url in attacks:
            with self.subTest(url=url), self.assertRaises(TemplateError):
                html(t'<a href={url}>link</a>')
        with self.assertRaises(TemplateError):
            html(t'<a href="java&#x73;cript:alert(1)">link</a>')
        for url in ['/path?q=1&x=2', '#main', 'https://example.com/path', 'mailto:hello@example.com', 'tel:+123']:
            self.assertIn('href=', render(html(t'<a href={url}>link</a>')).markup)
        with self.assertRaises(TemplateError):
            html(t'<img src={"mailto:bad@example.com"} alt="test">')

    def test_rejects_executable_and_dynamic_contexts(self):
        templates = [
            t'<script>{"bad"}</script>', t'<style>p {{ color: red }}</style>',
            t'<iframe src="/"></iframe>', t'<svg></svg>',
            t'<div onclick={"bad"}></div>', t'<div style={"color:red"}></div>',
            t'<div data-sui-click="forged"></div>',
            t'<{"div"}>hello</{"div"}>', t'<div {"title"}="hello"></div>',
            t'<!-- {"secret"} -->', t'<div layout="Grid(1, 1)"></div>',
            t'<button on_click={"run"}>run</button>',
            t'<p title={Grid(1, 1)}>test</p>', t'<p title="a{None}">test</p>',
        ]
        for template in templates:
            with self.subTest(template=template), self.assertRaises(TemplateError):
                html(template)

    def test_malformed_and_browser_reparenting_structures_fail(self):
        templates = [
            t'<p>unclosed', t'<b><i>bad</b></i>', t'<div/>', t'</p>',
            t'<p><div>invalid</div></p>', t'<div id="a" id="b"></div>',
            t'<table><tr><td>needs tbody</td></tr></table>',
            t'<ul>text<li>item</li></ul>',
            t'<!DOCTYPE html>', t'<?hello?>',
        ]
        for template in templates:
            with self.subTest(template=template), self.assertRaises(TemplateError):
                html(template)
        nested = html(t'<span><a href="/">inner</a></span>')
        with self.assertRaises(TemplateError):
            html(t'<a href="/">{nested}</a>')
        item = html(t'<li>item</li>')
        self.assertEqual(render(html(t'<ul>{item}</ul>')).markup, '<ul><li>item</li></ul>')
        with self.assertRaises(TemplateError):
            render(item)
        self.assertEqual(render(html(t'<textarea><b>text</b></textarea>')).markup,
                         '<textarea>&lt;b&gt;text&lt;/b&gt;</textarea>')
        with self.assertRaises(TemplateError):
            html(t'<textarea>{html(t"<strong>child</strong>")}</textarea>')

    def test_click_bindings_use_opaque_ids(self):
        calls = []
        def callback():
            calls.append('clicked')
        tree = html(t'<button type="button" on_click={callback}>Go</button>')
        first, second = render(tree), render(tree)
        self.assertEqual(len(first.events), 1)
        token = next(iter(first.events))
        self.assertIn(f'data-sui-click="{token}"', first.markup)
        self.assertNotIn('callback', first.markup)
        self.assertNotEqual(first.markup, second.markup)
        first.events[token]()
        self.assertEqual(calls, ['clicked'])
        with self.assertRaises(TemplateError):
            render(html(t'<div on_click={callback}>Go</div>'))


class GridRenderingTests(unittest.TestCase):
    def test_fit_css_and_relative_position_preserve_source_order(self):
        grid = Grid(4, 4)
        result = render(html(t'''<main layout={grid}>
            <section key="second" place={below("first", width=4, height=2)}>Second</section>
            <nav key="first" place={at(0, 0, 4, 2)}>First</nav>
        </main>'''))
        self.assertLess(result.markup.index('Second'), result.markup.index('First'))
        self.assertNotIn(' place=', result.markup)
        self.assertNotIn(' layout=', result.markup)
        self.assertIn('min(calc(100cqw / 4), calc(100cqh / 4))', result.css)
        self.assertIn('grid-row:3/span 2', result.css)
        self.assertIn('container:sui-g0/size', result.css)

    def test_reflow_breakpoint_and_nested_grid(self):
        grid = Grid(4, 6, mode="reflow", breakpoints=(
            Breakpoint(800, 8, 3, {'card': at(4, 0, 4, 3)}),
        ))
        result = render(html(t'''<main layout={grid}>
            <section key="card" place={at(0, 0, 4, 3)} layout={Grid(2, 2)}>
                <p key="child" place={at(0, 0, 2, 2)}>Nested</p>
            </section>
        </main>'''))
        self.assertIn('@container sui-g0 (min-width:800px)', result.css)
        self.assertIn('grid-column:5/span 4', result.css)
        self.assertIn('data-sui-viewport="g1" data-sui-key="card"', result.markup)
        self.assertIn('data-sui-grid="g1"', result.markup)

    def test_invalid_layout_directives(self):
        templates = [
            t'<main layout={Grid(2, 2)}><p>missing directives</p></main>',
            t'<p place={at(0, 0, 1, 1)}>orphan</p>',
            t'<main layout={Grid(2, 2)}>unplaced text</main>',
            t'<div><p key="a">one</p><p key="a">two</p></div>',
        ]
        for template in templates:
            with self.subTest(template=template), self.assertRaises(TemplateError):
                render(html(template))
        grid = Grid(4, 4, mode="reflow", breakpoints=(Breakpoint(800, 1, 1),))
        with self.assertRaises(BoundsError):
            render(html(t'<main layout={grid}><p key="a" place={at(0, 0, 2, 2)}>bad variant</p></main>'))


if __name__ == '__main__':
    unittest.main()
