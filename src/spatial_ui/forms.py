"""Bounded, immutable form values for the form-events-v1 profile."""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
import json
import re
from types import MappingProxyType

MAX_BODY = 16_384
MAX_TEXT = 4096
NAME = re.compile(r"[A-Za-z][A-Za-z0-9_-]{0,63}\Z")
KINDS = frozenset({"text", "textarea", "select", "checkbox"})
MESSAGES = {
    "edited": "Unsaved changes.", "saved": "Saved.", "loaded": "Loaded.",
    "deleted": "Deleted.", "invalid": "Check the marked fields.",
    "conflict": "The record changed. Refresh before merging your edits.",
    "error": "The request failed. Your edits are preserved.",
    "uncertain": "The save outcome is unknown. Reload to reconcile it.",
}
ERRORS = {"required": "This field is required.", "invalid": "Check this field.",
          "immutable": "This field cannot be changed."}


class FormError(ValueError):
    """A form declaration or payload does not match the selected profile."""


def _text(value):
    if type(value) is not str:
        raise FormError("Expected bounded UTF-8 text.")
    try:
        if len(value.encode("utf-8")) > MAX_TEXT:
            raise FormError("Expected bounded UTF-8 text.")
    except UnicodeError:
        raise FormError("Expected bounded UTF-8 text.") from None


@dataclass(frozen=True, slots=True)
class Field:
    name: str
    kind: str = "text"
    required: bool = True
    default: str | bool | None = None
    disabled: bool = False

    def __post_init__(self):
        if type(self.name) is not str or not NAME.fullmatch(self.name) or type(self.kind) is not str or self.kind not in KINDS:
            raise FormError("Select a supported field name and kind.")
        if type(self.required) is not bool or type(self.disabled) is not bool:
            raise FormError("Field flags must be booleans.")
        if self.disabled and self.required:
            raise FormError("A disabled field cannot satisfy a required field.")
        if self.default is not None:
            self.check(self.default)

    def check(self, value):
        if self.kind == "checkbox":
            if type(value) is not bool:
                raise FormError("Checkboxes require booleans.")
        else:
            _text(value)


@dataclass(frozen=True, slots=True)
class FormEvent:
    kind: str
    fields: Mapping[str, str | bool]

    def __post_init__(self):
        if type(self.kind) is not str or self.kind not in {"input", "change", "submit"} or not isinstance(self.fields, Mapping):
            raise FormError("Select a supported form event.")
        if not 0 <= len(self.fields) <= 64 or self.kind != "submit" and len(self.fields) != 1:
            raise FormError("Unexpected field count.")
        detached = dict(self.fields)
        for name, value in detached.items():
            if type(name) is not str or not NAME.fullmatch(name):
                raise FormError("Invalid field name.")
            if type(value) is not bool:
                _text(value)
        object.__setattr__(self, "fields", MappingProxyType(detached))


@dataclass(frozen=True, slots=True)
class FormSchema:
    fields: tuple[Field, ...]

    def __post_init__(self):
        fields = tuple(self.fields)
        if not 1 <= len(fields) <= 64 or any(not isinstance(field, Field) for field in fields):
            raise FormError("Declare between 1 and 64 typed fields.")
        if len({field.name for field in fields}) != len(fields):
            raise FormError("Field names must be unique.")
        object.__setattr__(self, "fields", fields)

    def admit(self, payload: dict) -> FormEvent:
        if type(payload) is not dict or payload.keys() != {"kind", "fields"} or type(payload["fields"]) is not dict:
            raise FormError("Unexpected form payload fields.")
        event = FormEvent(payload["kind"], payload["fields"])
        declared = {field.name: field for field in self.fields if not field.disabled}
        if event.fields.keys() - declared.keys():
            raise FormError("Unknown or disabled field.")
        values = dict(event.fields)
        for name, value in values.items():
            declared[name].check(value)
        if event.kind == "submit":
            for name, field in declared.items():
                if name not in values:
                    if field.required or field.default is None:
                        raise FormError("Missing declared field.")
                    values[name] = field.default
        return FormEvent(event.kind, values)


@dataclass(frozen=True, slots=True)
class FieldError:
    name: str
    code: str = "invalid"

    def __post_init__(self):
        if type(self.name) is not str or not NAME.fullmatch(self.name) or type(self.code) is not str or self.code not in ERRORS:
            raise FormError("Select a public field error.")

    def as_dict(self):
        return {"name": self.name, "code": self.code, "message": ERRORS[self.code]}


@dataclass(frozen=True, slots=True)
class FormResult:
    status: str = "edited"
    errors: tuple[FieldError, ...] = ()

    def __post_init__(self):
        errors = tuple(self.errors)
        if type(self.status) is not str or self.status not in MESSAGES or len(errors) > 64 or any(not isinstance(error, FieldError) for error in errors):
            raise FormError("Select a bounded public form result.")
        if len({error.name for error in errors}) != len(errors):
            raise FormError("Field errors must be unique.")
        object.__setattr__(self, "errors", errors)

    def as_dict(self):
        return {"status": self.status, "message": MESSAGES[self.status],
                "errors": [error.as_dict() for error in self.errors],
                "focus": self.errors[0].name if self.errors else None}


@dataclass(frozen=True, slots=True)
class FormBinding:
    schema: FormSchema
    callback: Callable[[FormEvent], FormResult]

    def __post_init__(self):
        if not isinstance(self.schema, FormSchema) or not callable(self.callback):
            raise FormError("Bind a trusted schema and callback.")


def _pairs(pairs):
    result = {}
    for name, value in pairs:
        if name in result:
            raise FormError("Duplicate JSON keys are unsupported.")
        _text(name)
        result[name] = value
    return result


def _constant(value):
    raise FormError("Non-finite JSON is unsupported.")


def decode_event(body: bytes) -> dict:
    """Decode the exact click or form envelope; schema admission is session-owned."""
    if type(body) is not bytes or not 0 < len(body) <= MAX_BODY:
        raise FormError("Expected a bounded event request.")
    try:
        result = json.loads(body.decode("utf-8"), object_pairs_hook=_pairs, parse_constant=_constant)
    except (ValueError, UnicodeError, RecursionError):
        raise FormError("Invalid event JSON.") from None
    if type(result) is not dict or result.keys() not in ({"event", "version"}, {"event", "version", "payload"}):
        raise FormError("Unexpected event envelope fields.")
    if type(result["event"]) is not str or not 1 <= len(result["event"]) <= 128:
        raise FormError("Expected a bounded event token.")
    _text(result["event"])
    if type(result["version"]) is not int or not 1 <= result["version"] <= 2**53 - 1:
        raise FormError("Expected a positive render version.")
    return result
