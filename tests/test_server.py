import contextlib
import http.client
import json
import re
import threading
import unittest
from importlib.resources import files

from spatial_ui import App, Component, State, Transition, html, render
from spatial_ui.demo import Dashboard, demo_stylesheet


class Counter(Component):
    count = State(0)
    def increment(self):
        self.count += 1
    def render(self):
        return html(t'<main><p>Count: {self.count}</p><button on_click={self.increment}>Add</button></main>')


@contextlib.contextmanager
def running(app):
    with app.make_server(0) as server:
        thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': 0.01}, daemon=True)
        thread.start()
        try:
            yield server.server_port
        finally:
            server.shutdown()
            thread.join(timeout=3)


class Client:
    def __init__(self, port):
        self.port = port
        self.cookie = ''
        self.csrf = ''

    def request(self, method='GET', path='/', body=None, headers=None):
        defaults = {'Cookie': self.cookie, 'Origin': f'http://127.0.0.1:{self.port}',
                    'Content-Type': 'application/json', 'X-Spatial-Token': self.csrf}
        defaults.update(headers or {})
        connection = http.client.HTTPConnection('127.0.0.1', self.port, timeout=5)
        connection.request(method, path, body=body, headers=defaults)
        response = connection.getresponse()
        data = response.read().decode('utf-8')
        result = response.status, dict(response.getheaders()), data
        connection.close()
        return result

    def page(self):
        status, headers, page = self.request()
        if status == 200:
            self.cookie = headers['Set-Cookie'].split(';')[0]
            self.csrf = re.search(r'name="sui-token" content="([^"]+)"', page)[1]
        return status, headers, page

    def event(self, page):
        return {'event': re.search(r'data-sui-click="([^"]+)"', page)[1],
                'version': int(re.search(r'data-version="(\d+)"', page)[1])}

    def post(self, payload, headers=None):
        return self.request('POST', '/_spatial/event', json.dumps(payload), headers)


class ServerTests(unittest.TestCase):
    def test_page_headers_assets_and_explicit_routes(self):
        with running(App(Counter, title='<test>', stylesheet='body { color: green; }')) as port:
            client = Client(port)
            status, headers, page = client.page()
            self.assertEqual(status, 200)
            self.assertIn('<title>&lt;test&gt;</title>', page)
            self.assertIn('HttpOnly; SameSite=Strict', headers['Set-Cookie'])
            self.assertIn("frame-ancestors 'none'", headers['Content-Security-Policy'])
            self.assertNotIn("'unsafe-inline'", headers['Content-Security-Policy'])
            nonce = re.search(r'nonce="([^"]+)"', page)[1]
            self.assertIn(f"'nonce-{nonce}'", headers['Content-Security-Policy'])
            self.assertEqual(headers['Cache-Control'], 'no-store')
            self.assertEqual(headers['X-Content-Type-Options'], 'nosniff')
            self.assertIn('ResizeObserver', client.request(path='/_spatial/runtime.js')[2])
            self.assertIn('color: green', client.request(path='/_spatial/theme.css')[2])
            for path in ('/../pyproject.toml', '/.git/config', '/_spatial/event', '/?other=1'):
                self.assertEqual(client.request(path=path)[0], 404)
            self.assertEqual(client.request('HEAD', '/_spatial/runtime.js')[2], '')
            self.assertEqual(client.request(headers={'Host': 'evil.example'})[0], 421)

    def test_callbacks_session_isolation_and_replay(self):
        with running(App(Counter)) as port:
            first, second = Client(port), Client(port)
            a, b = first.page()[2], second.page()[2]
            event = first.event(a)
            self.assertEqual(second.post(event)[0], 404)
            status, _, body = first.post(event)
            self.assertEqual(status, 200)
            self.assertIn('Count: 1', json.loads(body)['markup'])
            self.assertEqual(first.post(event)[0], 409)
            self.assertIn('Count: 0', second.page()[2])
            self.assertIn('Count: 1', first.page()[2])
            self.assertNotEqual(first.cookie, second.cookie)
            self.assertNotEqual(first.csrf, second.csrf)

    def test_local_previews_on_different_ports_keep_separate_cookies(self):
        with running(App(Counter)) as first_port, running(App(Counter)) as second_port:
            first, second = Client(first_port), Client(second_port)
            event = first.event(first.page()[2])
            second.page()
            self.assertNotEqual(first.cookie.split('=')[0], second.cookie.split('=')[0])
            first.cookie += '; ' + second.cookie
            self.assertEqual(first.post(event)[0], 200)
            self.assertIn('Count: 0', second.page()[2])

    def test_request_origin_token_and_payload_validation(self):
        with running(App(Counter)) as port:
            client = Client(port)
            event = client.event(client.page()[2])
            for headers, expected in [
                ({'Origin': 'https://evil.example'}, 403),
                ({'Origin': ''}, 403),
                ({'X-Spatial-Token': 'wrong'}, 403),
                ({'Cookie': ''}, 403),
                ({'Content-Type': 'text/plain'}, 415),
                ({'Content-Length': '9999999'}, 413),
                ({'Transfer-Encoding': 'chunked'}, 400),
            ]:
                with self.subTest(headers=headers):
                    self.assertEqual(client.post(event, headers)[0], expected)
            for payload in ([], {'event':'increment', 'version':1, 'args':[]},
                            {'event': event['event'], 'version':True},
                            {'event':{}, 'version':1}):
                self.assertEqual(client.post(payload)[0], 400)
            self.assertEqual(client.request('POST', '/_spatial/event', b'not json')[0], 400)
            self.assertEqual(client.request('POST', '/_spatial/event', b'\xff')[0], 400)
            self.assertEqual(client.post(event)[0], 200)

    def test_expiry_and_capacity_are_bounded(self):
        now = [0]
        with running(App(Counter, max_sessions=1, session_ttl=10, clock=lambda: now[0])) as port:
            first, second = Client(port), Client(port)
            event = first.event(first.page()[2])
            self.assertEqual(second.page()[0], 503)
            now[0] = 10
            self.assertEqual(first.post(event)[0], 403)
            self.assertEqual(second.page()[0], 200)

    def test_failed_callback_and_factory_do_not_expose_errors(self):
        class Failing(Counter):
            def increment(self):
                raise RuntimeError('private detail')
        with running(App(Failing)) as port:
            client = Client(port)
            event = client.event(client.page()[2])
            status, _, body = client.post(event)
            self.assertEqual(status, 500)
            self.assertNotIn('private detail', body)
            self.assertEqual(client.post(event)[0], 409)
        shared = Counter()
        with running(App(lambda: shared)) as port:
            self.assertEqual(Client(port).page()[0], 200)
            self.assertEqual(Client(port).page()[0], 500)

    def test_server_refuses_non_loopback_and_invalid_configuration(self):
        for host in ('0.0.0.0', '192.0.2.1', 'example.com', 'localhost', '::1'):
            with self.subTest(host=host), self.assertRaises(ValueError):
                App(Counter).make_server(0, host=host)
        for kwargs in ({'max_sessions':0}, {'session_ttl':False}):
            with self.assertRaises(ValueError):
                App(Counter, **kwargs)

    def test_demo_renders_all_modes_and_bundles_assets(self):
        dashboard = Dashboard()
        initial = render(dashboard.render())
        self.assertEqual(len(initial.events), 4)
        self.assertIn('@container sui-g0 (min-width:760px)', initial.css)
        self.assertIn('aria-pressed="false"', initial.markup)
        self.assertIn('data-sui-motion="260"', initial.markup)
        dashboard.move_panel()
        dashboard.toggle_canvas()
        dashboard.toggle_motion()
        changed = render(dashboard.render())
        self.assertIn('grid-column:6/span 2', changed.css)
        self.assertIn('data-sui-motion="0"', changed.markup)
        self.assertIn('canvas compact', changed.markup)
        self.assertIn('prefers-reduced-motion', demo_stylesheet())
        self.assertTrue(files('spatial_ui').joinpath('static', 'runtime.js').is_file())
        for kwargs in ({'duration':-1}, {'duration':2001}, {'easing':'javascript:bad'}):
            with self.assertRaises(ValueError):
                Transition(**kwargs)


if __name__ == '__main__':
    unittest.main()
