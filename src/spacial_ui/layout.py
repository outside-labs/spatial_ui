"""Resolve named spatial relationships without measuring browser content."""

import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from graphlib import CycleError, TopologicalSorter
from types import MappingProxyType
from typing import Literal

from spacial_ui.geometry import Rect, Size, integer


class LayoutError(ValueError):
    """A set of placements cannot form a valid layout."""


class UnknownTargetError(LayoutError):
    pass


class DependencyCycleError(LayoutError):
    pass


class BoundsError(LayoutError):
    pass


class CollisionError(LayoutError):
    pass


@dataclass(frozen=True, slots=True)
class Relative:
    target: str
    side: Literal["right", "left", "above", "below"]
    width: int
    height: int
    gap: int = 0
    align: str = "top"

    def __post_init__(self) -> None:
        if not isinstance(self.target, str) or not self.target:
            raise ValueError("target must be a nonempty name")
        Size(self.width, self.height)
        integer(self.gap, "gap", minimum=0)
        if self.side not in {"right", "left", "above", "below"}:
            raise ValueError("unknown relative side")
        allowed = {"top", "center", "bottom"} if self.side in {"left", "right"} else {
            "left", "center", "right"
        }
        if self.align not in allowed:
            raise ValueError(f"alignment for {self.side} must be one of {sorted(allowed)}")

    def resolve(self, target: Rect) -> Rect:
        if self.side in {"left", "right"}:
            offset = {"top": 0, "center": (target.height - self.height) // 2,
                      "bottom": target.height - self.height}[self.align]
            x = target.right + self.gap if self.side == "right" else target.x - self.gap - self.width
            return Rect(x, target.y + offset, self.width, self.height)
        offset = {"left": 0, "center": (target.width - self.width) // 2,
                  "right": target.width - self.width}[self.align]
        y = target.bottom + self.gap if self.side == "below" else target.y - self.gap - self.height
        return Rect(target.x + offset, y, self.width, self.height)


@dataclass(frozen=True, slots=True)
class Centered:
    target: str | None
    width: int
    height: int

    def __post_init__(self) -> None:
        if self.target is not None and (not isinstance(self.target, str) or not self.target):
            raise ValueError("target must be a nonempty name or None for the grid")
        Size(self.width, self.height)

    def resolve(self, target: Rect) -> Rect:
        # An odd remainder goes to the bottom/right, keeping whole-cell coordinates.
        return Rect(target.x + (target.width - self.width) // 2,
                    target.y + (target.height - self.height) // 2, self.width, self.height)


type Placement = Rect | Relative | Centered


def at(x: int, y: int, width: int, height: int) -> Rect:
    return Rect(x, y, width, height)


def right_of(target: str, *, width: int, height: int, gap: int = 0, align: str = "top") -> Relative:
    return Relative(target, "right", width, height, gap, align)


def left_of(target: str, *, width: int, height: int, gap: int = 0, align: str = "top") -> Relative:
    return Relative(target, "left", width, height, gap, align)


def above(target: str, *, width: int, height: int, gap: int = 0, align: str = "left") -> Relative:
    return Relative(target, "above", width, height, gap, align)


def below(target: str, *, width: int, height: int, gap: int = 0, align: str = "left") -> Relative:
    return Relative(target, "below", width, height, gap, align)


def center_in(target: str | None = None, *, width: int, height: int) -> Centered:
    return Centered(target, width, height)


def _placements(value: Mapping[str, Placement]) -> dict[str, Placement]:
    result = dict(value)
    for name, place in result.items():
        if not isinstance(name, str) or not name:
            raise ValueError("placement names must be nonempty strings")
        if not isinstance(place, (Rect, Relative, Centered)):
            raise TypeError(f"invalid placement for {name!r}")
    return result


@dataclass(frozen=True, slots=True)
class Breakpoint:
    """An inclusive minimum container width in CSS pixels."""

    min_width: int
    columns: int
    rows: int
    placements: Mapping[str, Placement] = field(default_factory=dict)

    def __post_init__(self) -> None:
        integer(self.min_width, "min_width", minimum=1)
        Size(self.columns, self.rows)
        object.__setattr__(self, "placements", MappingProxyType(_placements(self.placements)))


@dataclass(frozen=True, slots=True)
class Layout:
    columns: int
    rows: int
    rects: Mapping[str, Rect]

    def __post_init__(self) -> None:
        object.__setattr__(self, "rects", MappingProxyType(dict(self.rects)))


def _dimension(value: float, name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{name} must be a finite number")
    if not math.isfinite(value) or value < 0:
        raise ValueError(f"{name} must be finite and nonnegative")


@dataclass(frozen=True, slots=True)
class Grid:
    columns: int
    rows: int
    mode: Literal["fit", "reflow"] = "fit"
    breakpoints: tuple[Breakpoint, ...] = ()
    allow_overlap: bool = False

    def __post_init__(self) -> None:
        Size(self.columns, self.rows)
        if self.mode not in {"fit", "reflow"}:
            raise ValueError("mode must be 'fit' or 'reflow'")
        if type(self.allow_overlap) is not bool:
            raise TypeError("allow_overlap must be a bool")
        points = tuple(self.breakpoints)
        if any(not isinstance(point, Breakpoint) for point in points):
            raise TypeError("breakpoints must contain Breakpoint objects")
        widths = [point.min_width for point in points]
        if widths != sorted(set(widths)):
            raise ValueError("breakpoints must have distinct ascending minimum widths")
        if points and self.mode != "reflow":
            raise ValueError("breakpoints require reflow mode")
        object.__setattr__(self, "breakpoints", points)

    def _variant(self, container_width: float | None) -> Breakpoint | None:
        if container_width is None:
            return None
        _dimension(container_width, "container_width")
        return next((point for point in reversed(self.breakpoints)
                     if container_width >= point.min_width), None)

    def unit(self, width: float, height: float) -> float:
        """Physical size of one square unit; fit also accounts for available height."""
        _dimension(width, "width")
        _dimension(height, "height")
        variant = self._variant(width)
        columns, rows = (variant.columns, variant.rows) if variant else (self.columns, self.rows)
        return min(width / columns, height / rows) if self.mode == "fit" else width / columns

    def resolve(self, placements: Mapping[str, Placement], *,
                container_width: float | None = None) -> Layout:
        authored = _placements(placements)
        variant = self._variant(container_width)
        columns, rows = (variant.columns, variant.rows) if variant else (self.columns, self.rows)
        if variant:
            unknown = variant.placements.keys() - authored.keys()
            if unknown:
                raise UnknownTargetError(f"breakpoint overrides unknown elements: {sorted(unknown)}")
            authored.update(variant.placements)
        dependencies: dict[str, list[str]] = {}
        for name, place in authored.items():
            target = None if isinstance(place, Rect) else place.target
            if target is not None and target not in authored:
                raise UnknownTargetError(f"{name!r} refers to unknown target {target!r}")
            dependencies[name] = [target] if target is not None else []
        try:
            order = tuple(TopologicalSorter(dependencies).static_order())
        except CycleError as error:
            raise DependencyCycleError(f"placement cycle: {' -> '.join(error.args[1])}") from error
        bounds = Rect(0, 0, columns, rows)
        resolved: dict[str, Rect] = {}
        for name in order:
            place = authored[name]
            rect = place if isinstance(place, Rect) else place.resolve(
                bounds if place.target is None else resolved[place.target]
            )
            if not bounds.contains(rect):
                raise BoundsError(f"{name!r} {rect} is outside the {columns} by {rows} grid")
            if not self.allow_overlap:
                for other, occupied in resolved.items():
                    if occupied.overlaps(rect):
                        raise CollisionError(f"{name!r} overlaps {other!r}")
            resolved[name] = rect
        return Layout(columns, rows, {name: resolved[name] for name in authored})
