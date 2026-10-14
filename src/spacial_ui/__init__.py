"""A Python spatial UI engine with square grids and t-string templates."""

from importlib.metadata import version

from spacial_ui.geometry import Point, Rect, Size
from spacial_ui.layout import (
    BoundsError, Breakpoint, CollisionError, DependencyCycleError, Grid, Layout,
    LayoutError, UnknownTargetError, above, at, below, center_in, left_of, right_of,
)

__version__ = version("spacial-ui")

__all__ = [
    "BoundsError", "Breakpoint", "CollisionError", "DependencyCycleError", "Grid",
    "Layout", "LayoutError", "Point", "Rect", "Size", "UnknownTargetError",
    "__version__", "above", "at", "below", "center_in", "left_of", "right_of",
]
