"""A Python spatial UI engine with square grids and t-string templates."""

from importlib.metadata import version

from spatial_ui.components import (
    CallbackError, Component, EventError, Session, Snapshot, StaleEventError, State,
    UnknownEventError,
)
from spatial_ui.geometry import Point, Rect, Size
from spatial_ui.layout import (
    BoundsError, Breakpoint, CollisionError, DependencyCycleError, Grid, Layout,
    LayoutError, UnknownTargetError, above, at, below, center_in, left_of, right_of,
)
from spatial_ui.rendering import Rendered, render
from spatial_ui.motion import Transition
from spatial_ui.server import App
from spatial_ui.templates import Fragment, TemplateError, html

__version__ = version("spatial-ui")

__all__ = [
    "BoundsError", "Breakpoint", "CollisionError", "DependencyCycleError", "Grid",
    "Layout", "LayoutError", "Point", "Rect", "Size", "UnknownTargetError",
    "__version__", "above", "at", "below", "center_in", "left_of", "right_of",
    "Fragment", "Rendered", "TemplateError", "html", "render",
    "CallbackError", "Component", "EventError", "Session", "Snapshot",
    "StaleEventError", "State", "UnknownEventError",
    "App", "Transition",
]
