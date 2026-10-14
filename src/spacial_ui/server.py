"""A bounded, loopback-only development transport. Not a production web server."""

import hmac
import ipaddress
import json
import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass
from html import escape
from http.cookies import CookieError, SimpleCookie
from http.server import BaseHTTPRequestHandler, HTTPServer
from importlib.resources import files

from spacial_ui.components import (
    CallbackError, Component, EventError, Session, StaleEventError, UnknownEventError,
)

MAX_BODY = 16_384
COOKIE = "spacial_session"


@dataclass
class _BrowserSession:
    session: Session
    csrf: str
    nonce: str
    touched: float


class App:
    def __init__(self, factory: Callable[[], Component], *, title: str = "spacial_ui",
                 stylesheet: str = "", max_sessions: int = 128, session_ttl: int = 1800,
                 clock: Callable[[], float] = time.monotonic):
        if not callable(factory):
            raise TypeError("App requires a factory that creates a fresh Component")
        if type(max_sessions) is not int or max_sessions < 1:
            raise ValueError("max_sessions must be a positive integer")
        if type(session_ttl) is not int or session_ttl < 1:
            raise ValueError("session_ttl must be a positive integer in seconds")
        self.factory = factory
        self.title = title
        self.stylesheet = stylesheet
        self.max_sessions = max_sessions
        self.session_ttl = session_ttl
        self.clock = clock
        self._sessions: dict[str, _BrowserSession] = {}

    def _lookup(self, identifier: str | None) -> _BrowserSession | None:
        now = self.clock()
        for key in list(self._sessions):
            if now - self._sessions[key].touched >= self.session_ttl:
                del self._sessions[key]
        entry = self._sessions.get(identifier)
        if entry:
            entry.touched = now
        return entry

    def _create(self) -> tuple[str, _BrowserSession]:
        if len(self._sessions) >= self.max_sessions:
            raise OverflowError("session capacity reached")
        component = self.factory()
        if any(component is entry.session.component for entry in self._sessions.values()):
            raise ValueError("the component factory must return a fresh instance")
        identifier = secrets.token_urlsafe(32)
        entry = _BrowserSession(Session(component), secrets.token_urlsafe(32),
                                secrets.token_urlsafe(24), self.clock())
        self._sessions[identifier] = entry
        return identifier, entry

    def make_server(self, port: int = 8000, *, host: str = "127.0.0.1") -> HTTPServer:
        try:
            address = ipaddress.IPv4Address(host)
        except ipaddress.AddressValueError as error:
            raise ValueError("host must be an explicit IPv4 loopback address") from error
        if not address.is_loopback:
            raise ValueError("the development server only binds to loopback")
        if type(port) is not int or not 0 <= port <= 65535:
            raise ValueError("port must be an integer between 0 and 65535")
        app = self
        runtime = files("spacial_ui").joinpath("static", "runtime.js").read_bytes()

        class Handler(BaseHTTPRequestHandler):
            def setup(self):
                super().setup()
                self.connection.settimeout(3)

            def log_message(self, format, *args):
                pass

            def _send(self, status, body, content_type="application/json; charset=utf-8", *, extra=()):
                data = body.encode("utf-8") if isinstance(body, str) else body
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Referrer-Policy", "no-referrer")
                for name, value in extra:
                    self.send_header(name, value)
                self.end_headers()
                if self.command != "HEAD":
                    self.wfile.write(data)

            def _error(self, status, message):
                self._send(status, json.dumps({"error": message}))

            def _host(self):
                values = self.headers.get_all("Host", [])
                valid = {f"{host}:{self.server.server_port}", f"localhost:{self.server.server_port}"}
                if len(values) != 1 or values[0] not in valid:
                    self._error(421, "unrecognized local host")
                    return None
                return values[0]

            def _cookie(self):
                cookie = SimpleCookie()
                try:
                    cookie.load(self.headers.get("Cookie", ""))
                except CookieError:
                    return None
                name = f"{COOKIE}_{self.server.server_port}"
                return cookie[name].value if name in cookie else None

            def _session_cookie(self, identifier):
                name = f"{COOKIE}_{self.server.server_port}"
                return ("Set-Cookie", f"{name}={identifier}; Path=/; HttpOnly; SameSite=Strict; Max-Age={app.session_ttl}")

            def do_HEAD(self):
                self.do_GET()

            def do_GET(self):
                if not self._host():
                    return
                if self.path == "/_spacial/runtime.js":
                    self._send(200, runtime, "text/javascript; charset=utf-8")
                    return
                if self.path == "/_spacial/theme.css":
                    self._send(200, app.stylesheet, "text/css; charset=utf-8")
                    return
                if self.path != "/":
                    self._error(404, "not found")
                    return
                identifier = self._cookie()
                entry = app._lookup(identifier)
                try:
                    if entry is None:
                        identifier, entry = app._create()
                    snapshot = entry.session.render()
                except OverflowError:
                    self._error(503, "local session capacity reached; try again later")
                    return
                except Exception:
                    if identifier:
                        app._sessions.pop(identifier, None)
                    self._error(500, "page render failed")
                    return
                # Grid CSS is generated only from validated names, integers, and enums.
                page = (
                    '<!doctype html><html lang="en"><head><meta charset="utf-8">'
                    '<meta name="viewport" content="width=device-width, initial-scale=1">'
                    f'<title>{escape(app.title)}</title>'
                    f'<meta name="sui-token" content="{entry.csrf}">'
                    '<link rel="stylesheet" href="/_spacial/theme.css">'
                    f'<style id="sui-layout" nonce="{entry.nonce}">{snapshot.css}</style>'
                    '<script defer src="/_spacial/runtime.js"></script></head><body>'
                    f'<div id="sui-app" data-version="{snapshot.version}">{snapshot.markup}</div>'
                    '<p id="sui-status" role="status" aria-live="polite"></p>'
                    '<noscript>Enable JavaScript to use Python event handlers.</noscript>'
                    '</body></html>'
                )
                policy = (
                    "default-src 'none'; script-src 'self'; "
                    f"style-src 'self' 'nonce-{entry.nonce}'; "
                    "img-src 'self' https:; connect-src 'self'; form-action 'self'; "
                    "base-uri 'none'; frame-ancestors 'none'"
                )
                self._send(200, page, "text/html; charset=utf-8", extra=(
                    ("Content-Security-Policy", policy),
                    self._session_cookie(identifier),
                ))

            def do_POST(self):
                hostname = self._host()
                if not hostname:
                    return
                if self.path != "/_spacial/event":
                    self._error(404, "not found")
                    return
                if self.headers.get("Origin") != f"http://{hostname}":
                    self._error(403, "same-origin requests required")
                    return
                if self.headers.get("Content-Type", "").split(";", 1)[0].lower() != "application/json":
                    self._error(415, "JSON required")
                    return
                lengths = self.headers.get_all("Content-Length", [])
                if self.headers.get("Transfer-Encoding") or len(lengths) != 1 or not lengths[0].isascii() or not lengths[0].isdigit():
                    self._error(400, "one Content-Length is required")
                    return
                if len(lengths[0]) > 6 or not 0 < int(lengths[0]) <= MAX_BODY:
                    self._error(413, "request body too large or empty")
                    return
                identifier = self._cookie()
                entry = app._lookup(identifier)
                supplied = self.headers.get("X-Spacial-Token", "").encode("utf-8")
                if entry is None or not hmac.compare_digest(supplied, entry.csrf.encode("utf-8")):
                    self._error(403, "session expired or invalid request token; refresh the page")
                    return
                try:
                    body = self.rfile.read(int(lengths[0]))
                    if len(body) != int(lengths[0]):
                        raise ValueError("incomplete body")
                    payload = json.loads(body)
                    if not isinstance(payload, dict) or payload.keys() != {"event", "version"}:
                        raise ValueError("unexpected event fields")
                except TimeoutError:
                    self._error(408, "request timed out")
                    return
                except (ValueError, UnicodeError):
                    self._error(400, "invalid event payload")
                    return
                try:
                    snapshot = entry.session.dispatch(payload["event"], payload["version"])
                except StaleEventError:
                    self._error(409, "page changed; refresh the page")
                    return
                except UnknownEventError:
                    self._error(404, "unknown event")
                    return
                except EventError:
                    self._error(400, "invalid event")
                    return
                except CallbackError:
                    self._error(500, "callback failed; refresh the page")
                    return
                self._send(200, json.dumps({"version": snapshot.version,
                                           "markup": snapshot.markup, "css": snapshot.css}),
                           extra=(self._session_cookie(identifier),))

        return HTTPServer((host, port), Handler)

    def run(self, port: int = 8000) -> None:
        with self.make_server(port) as server:
            print(f"Local preview: http://127.0.0.1:{server.server_port}", flush=True)
            try:
                server.serve_forever()
            except KeyboardInterrupt:
                pass
