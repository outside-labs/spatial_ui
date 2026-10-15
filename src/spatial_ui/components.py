"""Explicit component state and transport-independent event sessions."""

import inspect
import threading
from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass
from typing import Self, overload

from spatial_ui.rendering import render
from spatial_ui.templates import Renderable


class State[T]:
    """A descriptor whose default is copied independently for each instance."""

    def __init__(self, default: T):
        self.default = default
        self.name = ""

    def __set_name__(self, owner: type, name: str) -> None:
        self.name = f"_sui_state_{name}"

    @overload
    def __get__(self, instance: None, owner: type | None = None) -> Self: ...

    @overload
    def __get__(self, instance: object, owner: type | None = None) -> T: ...

    def __get__(self, instance, owner=None):
        if instance is None:
            return self
        if self.name not in instance.__dict__:
            instance.__dict__[self.name] = deepcopy(self.default)
        return instance.__dict__[self.name]

    def __set__(self, instance: object, value: T) -> None:
        instance.__dict__[self.name] = value


class Component(Renderable):
    """Retain child instances on the component to retain their state."""


class EventError(ValueError):
    pass


class StaleEventError(EventError):
    pass


class UnknownEventError(EventError):
    pass


class CallbackError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class Snapshot:
    version: int
    markup: str
    css: str
    event_ids: tuple[str, ...]


def _validate_callback(callback: Callable[[], None]) -> None:
    for candidate in (callback, getattr(callback, "__call__", None)):
        if any(check(candidate) for check in (
            inspect.iscoroutinefunction, inspect.isgeneratorfunction, inspect.isasyncgenfunction
        )):
            raise EventError("click handlers must be synchronous functions")
    try:
        inspect.signature(callback).bind()
    except (TypeError, ValueError) as error:
        raise EventError("click handlers must accept a call without arguments") from error


class Session:
    """A component tree and its current event allowlist, owned by one browser."""

    def __init__(self, component: Component):
        if not isinstance(component, Component):
            raise TypeError("Session requires a Component instance")
        self.component = component
        self._version = 0
        self._callbacks: dict[str, Callable[[], None]] = {}
        self._lock = threading.RLock()

    def render(self) -> Snapshot:
        with self._lock:
            self._callbacks = {}
            self._version += 1
            rendered = render(self.component.render())
            for callback in rendered.events.values():
                _validate_callback(callback)
            self._callbacks = dict(rendered.events)
            return Snapshot(self._version, rendered.markup, rendered.css, tuple(rendered.events))

    def dispatch(self, event_id: str, version: int) -> Snapshot:
        with self._lock:
            if type(version) is not int or version < 1:
                raise EventError("version must be a positive integer")
            if not isinstance(event_id, str) or not event_id or len(event_id) > 128:
                raise EventError("event ID must be a nonempty bounded string")
            if version != self._version:
                raise StaleEventError("page changed; refresh before sending another event")
            callback = self._callbacks.get(event_id)
            if callback is None:
                raise UnknownEventError("unknown event")
            # Invalidate all bindings before running user code, including failure paths.
            self._callbacks = {}
            try:
                result = callback()
                if result is not None:
                    if inspect.iscoroutine(result) or inspect.isgenerator(result):
                        result.close()
                    raise TypeError("click handlers must return None")
                return self.render()
            except Exception as error:
                self._version += 1
                raise CallbackError("callback or rerender failed; refresh the page") from error
