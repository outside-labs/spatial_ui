"""Explicit, dependency-free bindings for the checked expose_api REST profile."""

from dataclasses import dataclass
import http.client
import ipaddress
import json
import math
import re
import threading
from types import MappingProxyType
from urllib.parse import quote, urlsplit

from spatial_ui.forms import FieldError, FormError, FormResult, FormSchema

KEY = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
PATH = re.compile(r"/[A-Za-z0-9_-]+(?:/[A-Za-z0-9_-]+)*\Z")


class BindingError(ValueError):
    """The selected resource operation is unavailable in the current state."""


class TransportError(RuntimeError):
    """A bounded REST exchange did not produce a usable response."""


def _revision(value):
    if type(value) is not str or not re.fullmatch(r"[1-9][0-9]{0,18}", value) or int(value) > 2**63 - 1:
        raise TransportError("Expected a canonical resource revision.")
    return value


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise TransportError("Invalid response JSON.")
        result[key] = value
    return result


def _constant(value):
    raise TransportError("Invalid response JSON.")


def _finite(value, depth=0):
    if depth > 32:
        raise TransportError("Invalid response JSON.")
    if type(value) is str:
        value.encode("utf-8")
    elif type(value) is float and not math.isfinite(value):
        raise TransportError("Invalid response JSON.")
    elif type(value) is list:
        for item in value:
            _finite(item, depth + 1)
    elif type(value) is dict:
        for key, item in value.items():
            key.encode("utf-8")
            _finite(item, depth + 1)


@dataclass(frozen=True, slots=True)
class Response:
    status: int
    data: object = None
    revision: str | None = None


class HTTPTransport:
    """Direct HTTP(S), one bounded connection per request, no redirects or cookies.

    Plain HTTP is limited to explicit loopback IPs. The timeout bounds socket
    operations, not DNS or the total request. Cancellation does not undo writes.
    """

    def __init__(self, origin: str, *, timeout: float = 5, max_body: int = 65536, headers=()):
        try:
            url = urlsplit(origin)
            port = url.port
            if url.scheme not in {"http", "https"} or not url.hostname or url.username is not None or url.password is not None or url.path not in {"", "/"} or url.query or url.fragment:
                raise ValueError
            if url.scheme == "http" and not ipaddress.ip_address(url.hostname).is_loopback:
                raise ValueError
        except (ValueError, TypeError):
            raise BindingError("Select one HTTPS origin or an explicit loopback HTTP origin.") from None
        if type(timeout) not in (int, float) or not math.isfinite(timeout) or not 0 < timeout <= 30:
            raise BindingError("Select a socket timeout between zero and 30 seconds.")
        if type(max_body) is not int or not 1 <= max_body <= 65536:
            raise BindingError("Select a body bound between 1 and 65536 bytes.")
        selected = {}
        for name, value in headers:
            if type(name) is not str or not re.fullmatch(r"[A-Za-z0-9-]+", name) or name.lower() in {"host", "content-length", "content-type", "if-match", "connection", "cookie", "transfer-encoding"} or name.lower() in selected:
                raise BindingError("Select unique application-owned headers.")
            if type(value) is not str or len(value) > 4096 or any(ord(char) < 32 or ord(char) > 126 for char in value):
                raise BindingError("Invalid application-owned header.")
            selected[name.lower()] = value
        self._url, self._port, self._headers = url, port, selected
        self.timeout, self.max_body = timeout, max_body

    def request(self, method, path, *, data=None, revision=None):
        if type(method) is not str or method not in {"GET", "POST", "PATCH", "DELETE"} or type(path) is not str or not re.fullmatch(r"/[A-Za-z0-9/_?&=.%~-]{0,8191}", path) or path.startswith("//"):
            raise BindingError("Select a supported method and origin-relative resource path.")
        headers = {**self._headers, "Accept": "application/json", "Connection": "close"}
        if revision is not None:
            headers["If-Match"] = '"' + _revision(revision) + '"'
        try:
            body = None if data is None else json.dumps(data, allow_nan=False, ensure_ascii=False).encode("utf-8")
            if body is not None:
                if len(body) > self.max_body:
                    raise ValueError
                headers["Content-Type"] = "application/json"
        except (ValueError, UnicodeError, TypeError, RecursionError):
            raise BindingError("Invalid or oversized request JSON.") from None
        connection_type = http.client.HTTPSConnection if self._url.scheme == "https" else http.client.HTTPConnection
        connection = connection_type(self._url.hostname, self._port, timeout=self.timeout)
        try:
            connection.request(method, path, body=body, headers=headers)
            response = connection.getresponse()
            raw = response.read(self.max_body + 1)
            if len(raw) > self.max_body:
                raise TransportError("Response exceeded its byte bound.")
            etags = response.headers.get_all("ETag", [])
            revision = None
            if etags:
                if len(etags) != 1 or not re.fullmatch(r'"[1-9][0-9]{0,18}"', etags[0]):
                    raise TransportError("Expected one canonical resource revision.")
                revision = _revision(etags[0][1:-1])
            if response.status == 204:
                if raw:
                    raise TransportError("Unexpected response body.")
                return Response(204, revision=revision)
            if response.headers.get_content_type() != "application/json":
                raise TransportError("Expected response JSON.")
            payload = json.loads(raw.decode("utf-8"), object_pairs_hook=_pairs, parse_constant=_constant)
            _finite(payload)
            return Response(response.status, payload, revision)
        except (OSError, http.client.HTTPException, ValueError, UnicodeError, RecursionError):
            raise TransportError("The REST exchange failed.") from None
        finally:
            connection.close()


@dataclass(frozen=True, slots=True)
class ResourceSpec:
    path: str
    schema: FormSchema
    visible: tuple[str, ...]
    key_field: str = "key"

    def __post_init__(self):
        visible = tuple(self.visible)
        if type(self.path) is not str or not PATH.fullmatch(self.path) or not isinstance(self.schema, FormSchema):
            raise BindingError("Select an explicit resource path and typed form schema.")
        names = {field.name for field in self.schema.fields}
        identity = next((field for field in self.schema.fields if field.name == self.key_field), None)
        if identity is None or identity.kind != "text" or identity.disabled:
            raise BindingError("The resource identity must be a writable text field.")
        if any(type(name) is not str for name in visible):
            raise BindingError("Select explicit visible field names.")
        if self.key_field not in names or not names <= set(visible) or len(visible) > 64 or len(set(visible)) != len(visible):
            raise BindingError("Visible fields must include the identity and all form fields.")
        if any(type(name) is not str or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,63}", name) for name in visible):
            raise BindingError("Select explicit visible field names.")
        object.__setattr__(self, "visible", visible)

    def project(self, data):
        if type(data) is not dict or self.key_field not in data:
            raise TransportError("Expected a resource object.")
        result = {name: data[name] for name in self.visible if name in data}
        for field in self.schema.fields:
            if field.name not in result:
                raise TransportError("Resource omitted a declared field.")
            try:
                field.check(result[field.name])
            except FormError:
                raise TransportError("Resource field does not match its declaration.") from None
        if type(result[self.key_field]) is not str or not KEY.fullmatch(result[self.key_field]):
            raise TransportError("Expected a bounded domain key.")
        # Additional read-only fields use the same finite text/boolean profile.
        for value in result.values():
            if type(value) is not bool and (type(value) is not str or len(value.encode("utf-8")) > 4096):
                raise TransportError("Unsupported visible field value.")
        return MappingProxyType(result)


class ResourceClient:
    def __init__(self, transport, spec: ResourceSpec):
        if not isinstance(spec, ResourceSpec) or not callable(getattr(transport, "request", None)):
            raise BindingError("Select a resource specification and explicit transport.")
        self.transport, self.spec = transport, spec

    def execute(self, operation, *, key=None, data=None, revision=None):
        methods = {"load": "GET", "create": "POST", "update": "PATCH", "delete": "DELETE"}
        if operation not in methods:
            raise BindingError("Select a supported resource operation.")
        path = self.spec.path
        if operation != "create":
            if type(key) is not str or not KEY.fullmatch(key):
                raise BindingError("Select a bounded domain key.")
            path += "/" + quote(key, safe="")
        if operation in {"update", "delete"}:
            _revision(revision)
        return self.transport.request(methods[operation], path, data=data, revision=revision)

    def list(self, *, offset=0, limit=100):
        if type(offset) is not int or not 0 <= offset <= 10000 or type(limit) is not int or not 1 <= limit <= 1000:
            raise BindingError("Select bounded pagination.")
        response = self.transport.request("GET", f"{self.spec.path}?offset={offset}&limit={limit}")
        if response.status != 200 or type(response.data) is not list or len(response.data) > limit:
            raise TransportError("Expected a bounded resource page.")
        return tuple(self.spec.project(item) for item in response.data)


@dataclass(frozen=True, slots=True)
class _Pending:
    generation: int
    sequence: int
    operation: str
    key: str
    data: object
    revision: str | None


class ResourceBinding:
    """One editor's detached drafts, revisions and request completion ownership."""

    def __init__(self, client: ResourceClient):
        if not isinstance(client, ResourceClient):
            raise BindingError("Select an explicit resource client.")
        self.client = client
        self._lock = threading.RLock()
        self._draft = {field.name: field.default if field.default is not None else (False if field.kind == "checkbox" else "")
                       for field in client.spec.schema.fields if not field.disabled}
        self._changed = {}
        self._sequence = self._generation = 0
        self._active = None
        self._uncertain = False
        self.committed, self.revision = None, None
        self.status, self.errors = "idle", ()

    @property
    def draft(self):
        with self._lock:
            return MappingProxyType(dict(self._draft))

    @property
    def dirty(self):
        with self._lock:
            return bool(self._changed)

    def edit(self, values):
        with self._lock:
            if self.status == "closed":
                raise BindingError("The editor is closed.")
            admitted = [self.client.spec.schema.admit({"kind": "input", "fields": {name: value}}) for name, value in values.items()]
            for event in admitted:
                self._sequence += 1
                for name, value in event.fields.items():
                    self._draft[name] = value
                    self._changed[name] = self._sequence

    def begin(self, operation, *, key=None):
        with self._lock:
            if self.status == "closed" or self._active is not None:
                raise BindingError("The editor is closed or a request is pending.")
            if operation not in {"load", "create", "update", "delete"}:
                raise BindingError("Select a supported resource operation.")
            if self._uncertain and operation != "load":
                raise BindingError("Reload to reconcile the previous write.")
            identity = self.client.spec.key_field
            key = key if key is not None else self._draft[identity]
            if type(key) is not str or not KEY.fullmatch(key):
                raise BindingError("Select a bounded domain key.")
            if self.committed is not None and key != self.committed[identity] and (operation != "load" or self.dirty):
                raise BindingError("Resolve edits before switching resource identity.")
            if operation in {"update", "delete"} and (self.committed is None or self.revision is None):
                raise BindingError("Load a revision before changing a record.")
            if operation == "create" and (self.committed is not None or key != self._draft[identity]):
                raise BindingError("Create requires a new editor and its declared identity.")
            data = None
            if operation in {"create", "update"}:
                data = dict(self.client.spec.schema.admit({"kind": "submit", "fields": dict(self._draft)}).fields)
                if operation == "update":
                    data.pop(identity)
            self._generation += 1
            ticket = _Pending(self._generation, self._sequence, operation, key,
                              MappingProxyType(data) if data is not None else None, self.revision if operation in {"update", "delete"} else None)
            self._active = ticket
            self.status, self.errors = ("loading" if operation == "load" else "saving"), ()
            return ticket

    def complete(self, ticket, response):
        with self._lock:
            if self._active is not ticket:
                return False
            self._active = None
            try:
                expected = {"load": 200, "create": 201, "update": 200, "delete": 204}[ticket.operation]
                if response.status != expected:
                    self.status = "conflict" if response.status == 409 else "invalid" if response.status == 422 else "error"
                    details = response.data.get("error", {}).get("details", []) if type(response.data) is dict else []
                    errors = {}
                    writable = {field.name for field in self.client.spec.schema.fields if not field.disabled}
                    if type(details) is list:
                        for detail in details[:64]:
                            if type(detail) is dict and type(detail.get("path")) is list and detail["path"] and type(detail["path"][0]) is str and detail["path"][0] in writable:
                                name = detail["path"][0]
                                code = detail.get("code")
                                errors[name] = FieldError(name, code if code in ("required", "immutable") else "invalid")
                    self.errors = tuple(errors.values())
                    if self._uncertain:
                        self.status = "uncertain"
                    return True
                if ticket.operation == "delete":
                    self.committed, self.revision, self.status = None, None, "deleted"
                    self._uncertain = False
                    return True
                projected = self.client.spec.project(response.data)
                revision = _revision(response.revision)
                if projected[self.client.spec.key_field] != ticket.key:
                    raise TransportError("Response identity changed.")
                self.committed, self.revision = projected, revision
                self._uncertain = False
                for name in self._draft:
                    if name not in self._changed or ticket.operation != "load" and self._changed[name] <= ticket.sequence:
                        self._draft[name] = projected[name]
                        self._changed.pop(name, None)
                self.status = "loaded" if ticket.operation == "load" else "saved"
            except (TransportError, FormError, AttributeError, TypeError, UnicodeError):
                self._uncertain = self._uncertain or ticket.operation != "load"
                self.status = "uncertain" if self._uncertain else "error"
            return True

    def perform(self, operation, *, key=None):
        ticket = self.begin(operation, key=key)
        try:
            response = self.client.execute(operation, key=ticket.key,
                                           data=dict(ticket.data) if ticket.data is not None else None, revision=ticket.revision)
        except Exception:
            with self._lock:
                if self._active is ticket:
                    self._active = None
                    self._uncertain = self._uncertain or operation != "load"
                    self.status = "uncertain" if self._uncertain else "error"
            return self.status
        self.complete(ticket, response)
        return self.status

    def cancel(self):
        with self._lock:
            if self._active is not None:
                self._uncertain = self._uncertain or self._active.operation != "load"
                self.status = "uncertain" if self._uncertain else "error"
                self._active = None

    def close(self):
        with self._lock:
            self._active = None
            self.status = "closed"

    def result(self):
        with self._lock:
            status = self.status if self.status in {"loaded", "saved", "deleted", "invalid", "conflict", "error", "uncertain"} else "edited"
            return FormResult(status, self.errors)
