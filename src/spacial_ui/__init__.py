"""A Python spatial UI engine with square grids and t-string templates."""

from importlib.metadata import version

from spacial_ui.components import (
    CallbackError, Component, EventError, Session, Snapshot, StaleEventError, State,
    UnknownEventError,
)
from spacial_ui.geometry import Point, Rect, Size
from spacial_ui.layout import (
    BoundsError, Breakpoint, CollisionError, DependencyCycleError, Grid, Layout,
    LayoutError, UnknownTargetError, above, at, below, center_in, left_of, right_of,
)
from spacial_ui.rendering import Rendered, render
from spacial_ui.templates import Fragment, TemplateError, html

__version__ = version("spacial-ui")

__all__ = [
    "BoundsError", "Breakpoint", "CollisionError", "DependencyCycleError", "Grid",
    "Layout", "LayoutError", "Point", "Rect", "Size", "UnknownTargetError",
    "__version__", "above", "at", "below", "center_in", "left_of", "right_of",
    "Fragment", "Rendered", "TemplateError", "html", "render",
    "CallbackError", "Component", "EventError", "Session", "Snapshot",
    "StaleEventError", "State", "UnknownEventError",
]
