"""Explicit same-origin configuration for the read-only resource event profile."""

from dataclasses import dataclass
import json

from .rest import BindingError, KEY, PATH, ResourceSpec


@dataclass(frozen=True, slots=True)
class LiveConfig:
    """Browser-owned single-record snapshots; checked writes remain REST-owned.

    The application routes this static collection path on the UI's exact origin
    and verifies its own HttpOnly identity cookie. UI session cookies do not
    authenticate API requests. Configuration never contains credentials.
    """

    path: str
    max_message_bytes: int = 65536
    retry_limit: int = 5
    retry_delay_ms: int = 500
    handshake_timeout_ms: int = 5000

    def __post_init__(self):
        if type(self.path) is not str or len(self.path) > 512 or not PATH.fullmatch(self.path):
            raise BindingError("Select a static same-origin collection path.")
        for name, minimum, maximum in (
            ("max_message_bytes", 1024, 65536), ("retry_limit", 0, 8),
            ("retry_delay_ms", 100, 5000), ("handshake_timeout_ms", 100, 30000),
        ):
            value = getattr(self, name)
            if type(value) is not int or not minimum <= value <= maximum:
                raise BindingError(f"Select a bounded {name}.")

    def declaration(self, spec: ResourceSpec, baseline: dict) -> str:
        """Return bounded JSON for trusted markup, projected through the REST spec.

        No domain revision is exported: a snapshot cannot establish an If-Match
        value. Template escaping is still required at the attribute boundary.
        """
        if not isinstance(spec, ResourceSpec):
            raise BindingError("Select an explicit resource specification.")
        value = dict(spec.project(baseline))
        key = value[spec.key_field]
        if not KEY.fullmatch(key):
            raise BindingError("Select a bounded domain key.")
        fields = {name: "boolean" if type(item) is bool else "text" for name, item in value.items()}
        result = json.dumps({"v": 1, "path": self.path + "/" + key,
            "key": key, "keyField": spec.key_field, "fields": fields, "baseline": value,
            "maxMessageBytes": self.max_message_bytes, "retryLimit": self.retry_limit,
            "retryDelayMs": self.retry_delay_ms, "handshakeTimeoutMs": self.handshake_timeout_ms},
            ensure_ascii=False, allow_nan=False, separators=(",", ":"))
        if len(result.encode("utf-8")) > 65536:
            raise BindingError("Live configuration exceeds its size limit.")
        return result
