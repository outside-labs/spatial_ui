"""Bounded native ASGI HTTP UI with one lifespan-owned synchronous worker."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
import hmac
import ipaddress
import json
import math
import os
import re
import secrets
from importlib.resources import files
from urllib.parse import urlsplit

from .components import CallbackError, StaleEventError, UnknownEventError
from .forms import MAX_BODY, decode_event
from .server import App, _document


class _Reject(Exception):
    def __init__(self, status, message):
        self.status, self.message = status, message


class _Disconnected(Exception):
    pass


@dataclass(eq=False)
class _Ticket:
    future: object = None
    finished: bool = False


def _origin(value):
    if type(value) is not str or not value.isascii() or len(value) > 512:
        raise ValueError("origin must be a canonical ASCII HTTP(S) origin")
    try:
        parts = urlsplit(value)
        port = parts.port
        if (parts.scheme not in ("http", "https") or not parts.hostname or parts.username is not None
                or parts.password is not None or parts.path or parts.query or parts.fragment
                or not re.fullmatch(r"[A-Za-z0-9.:[\]-]+", parts.netloc) or port == 0):
            raise ValueError
        if parts.scheme == "http":
            if parts.hostname != "localhost" and not ipaddress.ip_address(parts.hostname).is_loopback:
                raise ValueError
        host = parts.hostname.lower()
        host = f"[{host}]" if ":" in host else host
        canonical = f"{parts.scheme}://{host}"
        if port is not None and port != (443 if parts.scheme == "https" else 80):
            canonical += f":{port}"
        if canonical != value:
            raise ValueError
    except ValueError:
        raise ValueError("origin must be canonical HTTPS or explicit HTTP loopback") from None
    return canonical, canonical.split("://", 1)[1], parts.scheme == "https"


def _one(headers, name):
    values = [value for key, value in headers if key.lower() == name]
    if len(values) > 1:
        raise _Reject(400, "duplicate request header")
    return values[0] if values else None


class ASGIApp:
    """A single-process UI adapter; the caller supplies the ASGI server."""

    def __init__(self, app: App, *, origin: str, pending_limit: int = 16,
                 body_timeout: float = 3.0, max_response_size: int = 1048576):
        if not isinstance(app, App):
            raise TypeError("ASGIApp requires an App configuration")
        self.origin, self._host, self._secure = _origin(origin)
        if type(pending_limit) is not int or not 1 <= pending_limit <= 1024:
            raise ValueError("pending_limit must be between 1 and 1024")
        if type(body_timeout) not in (float, int) or not math.isfinite(body_timeout) or body_timeout <= 0:
            raise ValueError("body_timeout must be positive and finite")
        if type(max_response_size) is not int or max_response_size < 1024:
            raise ValueError("max_response_size must be at least 1024")
        self._app = App(app.factory, title=app.title, stylesheet=app.stylesheet,
                        max_sessions=app.max_sessions, session_ttl=app.session_ttl, clock=app.clock)
        self.pending_limit, self.body_timeout, self.max_response_size = pending_limit, body_timeout, max_response_size
        self.state = {}
        self._executor = self._loop = self._pid = self._idle = self._close_task = None
        self._tickets = set()
        self._closing = False

    async def __aenter__(self):
        if self._executor is not None:
            raise RuntimeError("ASGI UI is already started")
        assets = {name: files("spatial_ui").joinpath("static", name).read_bytes() for name in ("runtime.js", "live.js")}
        self._loop, self._pid = asyncio.get_running_loop(), os.getpid()
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="spatial-ui")
        self._idle = asyncio.Event()
        self._idle.set()
        self._closing, self._close_task = False, None
        self._cookie_name = ("__Host-spatial_" if self._secure else "spatial_") + secrets.token_hex(8)
        self._assets = assets
        return self

    def _owner(self):
        if self._loop is not asyncio.get_running_loop() or self._pid != os.getpid():
            raise RuntimeError("ASGI UI must stay in its opening event loop and process")

    async def __aexit__(self, *_):
        await self.aclose()

    async def aclose(self):
        if self._executor is None:
            return
        self._owner()
        if self._close_task is None:
            self._closing = True
            self._close_task = asyncio.create_task(self._drain())
        canceled = False
        while not self._close_task.done():
            try:
                await asyncio.shield(self._close_task)
            except asyncio.CancelledError:
                canceled = True
        self._close_task.result()
        if canceled:
            raise asyncio.CancelledError

    async def _drain(self):
        await self._idle.wait()
        executor = self._executor
        try:
            await asyncio.wrap_future(executor.submit(self._app._sessions.clear))
        finally:
            executor.shutdown(wait=True, cancel_futures=True)
            self._executor = self._loop = self._pid = None

    def _release(self, ticket):
        if ticket in self._tickets and ticket.finished and (ticket.future is None or ticket.future.done()):
            self._tickets.discard(ticket)
            if not self._tickets:
                self._idle.set()

    async def _execute(self, callback, ticket, receive):
        ticket.future = self._executor.submit(callback)
        ticket.future.add_done_callback(lambda future: self._loop.call_soon_threadsafe(self._release, ticket))
        result = asyncio.wrap_future(ticket.future)
        # Retrieve abandoned failures without ever logging callback exception text.
        result.add_done_callback(lambda future: None if future.cancelled() else future.exception())
        async def disconnected():
            while True:
                event = await receive()
                if event["type"] == "http.disconnect":
                    return
                raise RuntimeError("Expected an ASGI disconnect after request completion")
        monitor = asyncio.create_task(disconnected())
        try:
            done, _ = await asyncio.wait((result, monitor), return_when=asyncio.FIRST_COMPLETED)
            if monitor in done:
                monitor.result()
                raise _Disconnected
            return result.result()
        except BaseException:
            ticket.future.cancel()
            raise
        finally:
            monitor.cancel()
            await asyncio.gather(monitor, return_exceptions=True)

    async def __call__(self, scope, receive, send):
        if scope["type"] == "lifespan":
            await self._lifespan(receive, send)
            return
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1008})
            return
        if scope["type"] != "http":
            raise RuntimeError("Unsupported ASGI scope")
        ticket = _Ticket()
        try:
            await self._http(scope, receive, send, ticket)
        finally:
            # A canceled dispatched callback retains its admission slot until done.
            ticket.finished = True
            self._release(ticket)

    async def _http(self, scope, receive, send, ticket):
        try:
            if self._executor is None or self._closing:
                raise _Reject(503, "UI is not accepting requests")
            self._owner()
            if len(self._tickets) >= self.pending_limit:
                raise _Reject(503, "UI request capacity reached; try again later")
            self._tickets.add(ticket)
            self._idle.clear()
            headers = tuple(scope.get("headers", ()))
            if (_one(headers, b"host") or b"").lower() != self._host.encode("ascii"):
                raise _Reject(421, "unrecognized application host")
            path, method = scope["path"], scope["method"]
            if scope.get("root_path") or scope.get("query_string") or path not in (
                    "/", "/_spatial/runtime.js", "/_spatial/live.js", "/_spatial/theme.css", "/_spatial/event"):
                raise _Reject(404, "not found")
            if method not in (("POST",) if path == "/_spatial/event" else ("GET", "HEAD")):
                raise _Reject(405, "method not allowed")
            if method == "POST":
                if _one(headers, b"origin") != self.origin.encode("ascii"):
                    raise _Reject(403, "same-origin requests required")
                media = _one(headers, b"content-type") or b""
                if media.split(b";", 1)[0].strip().lower() != b"application/json":
                    raise _Reject(415, "JSON required")
            body = await self._body(headers, receive)
            if method != "POST" and body:
                raise _Reject(400, "request body not supported")
            response = await self._execute(lambda: self._dispatch(path, headers, body), ticket, receive)
        except _Disconnected:
            return
        except _Reject as error:
            response = self._error(error.status, error.message)
        except asyncio.CancelledError:
            raise
        except Exception:
            response = self._error(500, "UI request failed; refresh the page")
        status, headers, body = response
        await send({"type": "http.response.start", "status": status, "headers": headers})
        await send({"type": "http.response.body", "body": b"" if scope["method"] == "HEAD" else body})

    async def _body(self, headers, receive):
        raw = _one(headers, b"content-length")
        if raw is not None and not re.fullmatch(rb"[0-9]{1,10}", raw):
            raise _Reject(400, "invalid content length")
        expected = int(raw) if raw is not None else None
        if expected is not None and expected > MAX_BODY:
            raise _Reject(413, "request body too large")
        body = bytearray()
        try:
            async with asyncio.timeout(self.body_timeout):
                while True:
                    event = await receive()
                    if event["type"] == "http.disconnect":
                        raise _Disconnected
                    if event["type"] != "http.request" or type(event.get("body", b"")) is not bytes:
                        raise RuntimeError("Expected an ASGI HTTP request")
                    chunk = event.get("body", b"")
                    if len(body) + len(chunk) > MAX_BODY:
                        raise _Reject(413, "request body too large")
                    body.extend(chunk)
                    if not event.get("more_body", False):
                        break
        except TimeoutError:
            raise _Reject(408, "request body timed out") from None
        if expected is not None and expected != len(body):
            raise _Reject(400, "content length does not match the body")
        return bytes(body)

    def _cookie(self, headers):
        values = []
        for name, raw in headers:
            if name.lower() == b"cookie":
                for part in raw.split(b";"):
                    key, separator, value = part.strip().partition(b"=")
                    if key == self._cookie_name.encode("ascii"):
                        if not separator or not re.fullmatch(rb"[A-Za-z0-9_-]{43}", value):
                            raise _Reject(400, "invalid session cookie")
                        values.append(value.decode("ascii"))
        if len(values) > 1:
            raise _Reject(400, "duplicate session cookie")
        return values[0] if values else None

    def _session_cookie(self, identifier):
        value = f"{self._cookie_name}={identifier}; Path=/; HttpOnly; SameSite=Strict; Max-Age={self._app.session_ttl}"
        if self._secure:
            value += "; Secure"
        return b"set-cookie", value.encode("ascii")

    def _response(self, status, body, media="application/json; charset=utf-8", *, extra=()):
        body = body.encode("utf-8") if isinstance(body, str) else body
        if len(body) > self.max_response_size:
            raise _Reject(500, "UI response exceeds its size limit; refresh the page")
        return status, [(b"content-type", media.encode()), (b"content-length", str(len(body)).encode()),
            (b"cache-control", b"no-store"), (b"x-content-type-options", b"nosniff"),
            (b"referrer-policy", b"no-referrer"), *extra], body

    def _error(self, status, message):
        return self._response(status, json.dumps({"error": message}))

    def _dispatch(self, path, headers, body):
        if path in ("/_spatial/runtime.js", "/_spatial/live.js"):
            return self._response(200, self._assets[path.rsplit("/", 1)[1]], "text/javascript; charset=utf-8")
        if path == "/_spatial/theme.css":
            return self._response(200, self._app.stylesheet, "text/css; charset=utf-8")
        identifier = self._cookie(headers)
        entry = self._app._lookup(identifier)
        if path == "/":
            try:
                if entry is None:
                    identifier, entry = self._app._create()
                page, policy = _document(self._app, entry, entry.session.render())
                return self._response(200, page, "text/html; charset=utf-8", extra=(
                    (b"content-security-policy", policy.encode("ascii")), self._session_cookie(identifier)))
            except OverflowError:
                raise _Reject(503, "session capacity reached; try again later") from None
            except BaseException:
                self._app._sessions.pop(identifier, None)
                raise
        supplied = _one(headers, b"x-spatial-token") or b""
        if entry is None or not hmac.compare_digest(supplied, entry.csrf.encode("ascii")):
            raise _Reject(403, "session expired or invalid request token; refresh the page")
        try:
            payload = decode_event(body)
            snapshot = entry.session.dispatch(payload["event"], payload["version"], payload.get("payload"))
        except (ValueError, UnicodeError) as error:
            if isinstance(error, StaleEventError):
                raise _Reject(409, "page changed; refresh the page") from None
            if isinstance(error, UnknownEventError):
                raise _Reject(404, "unknown event") from None
            raise _Reject(400, "invalid event payload") from None
        except CallbackError:
            raise _Reject(500, "callback failed; refresh the page") from None
        return self._response(200, json.dumps({"version": snapshot.version, "markup": snapshot.markup,
            "css": snapshot.css, "form": snapshot.form_result.as_dict() if snapshot.form_result else None}),
            extra=(self._session_cookie(identifier),))

    async def _lifespan(self, receive, send):
        event = await receive()
        if event["type"] != "lifespan.startup":
            raise RuntimeError("Expected lifespan.startup")
        try:
            await self.__aenter__()
        except Exception:
            await send({"type": "lifespan.startup.failed", "message": "UI startup failed"})
            return
        try:
            await send({"type": "lifespan.startup.complete"})
            event = await receive()
            if event["type"] != "lifespan.shutdown":
                raise RuntimeError("Expected lifespan.shutdown")
        finally:
            await self.aclose()
        await send({"type": "lifespan.shutdown.complete"})
