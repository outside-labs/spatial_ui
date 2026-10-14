import math
import unittest
from dataclasses import FrozenInstanceError

from spacial_ui import (
    BoundsError, Breakpoint, CollisionError, DependencyCycleError, Grid, Point,
    Rect, Size, UnknownTargetError, above, at, below, center_in, left_of, right_of,
)


class GeometryTests(unittest.TestCase):
    def test_geometry_and_touching_edges(self):
        rect = Rect(2, 3, 4, 5)
        self.assertEqual(rect.position, Point(2, 3))
        self.assertEqual(rect.size, Size(4, 5))
        self.assertEqual((rect.right, rect.bottom), (6, 8))
        self.assertTrue(rect.contains(Rect(3, 4, 1, 1)))
        self.assertTrue(rect.overlaps(Rect(5, 7, 2, 2)))
        self.assertFalse(rect.overlaps(Rect(6, 3, 1, 1)))
        with self.assertRaises(FrozenInstanceError):
            rect.x = 0

    def test_invalid_geometry(self):
        for value in (True, 1.5, "1", None):
            with self.subTest(value=value), self.assertRaises(TypeError):
                Rect(value, 0, 1, 1)
        for width, height in ((0, 1), (1, -1)):
            with self.assertRaises(ValueError):
                Size(width, height)


class LayoutTests(unittest.TestCase):
    def test_forward_references_preserve_authored_order(self):
        layout = Grid(12, 8).resolve({
            "card": right_of("sidebar", width=6, height=3, gap=1),
            "sidebar": below("nav", width=3, height=6),
            "nav": at(0, 0, 12, 2),
        })
        self.assertEqual(list(layout.rects), ["card", "sidebar", "nav"])
        self.assertEqual(layout.rects["card"], Rect(4, 2, 6, 3))
        with self.assertRaises(TypeError):
            layout.rects["card"] = Rect(0, 0, 1, 1)

    def test_all_relative_directions_and_alignments(self):
        target = at(4, 4, 4, 4)
        cases = [
            (right_of, "top", Rect(9, 4, 2, 2)),
            (right_of, "center", Rect(9, 5, 2, 2)),
            (right_of, "bottom", Rect(9, 6, 2, 2)),
            (left_of, "top", Rect(1, 4, 2, 2)),
            (left_of, "center", Rect(1, 5, 2, 2)),
            (left_of, "bottom", Rect(1, 6, 2, 2)),
            (below, "left", Rect(4, 9, 2, 2)),
            (below, "center", Rect(5, 9, 2, 2)),
            (below, "right", Rect(6, 9, 2, 2)),
            (above, "left", Rect(4, 1, 2, 2)),
            (above, "center", Rect(5, 1, 2, 2)),
            (above, "right", Rect(6, 1, 2, 2)),
        ]
        for factory, align, expected in cases:
            with self.subTest(side=factory.__name__, align=align):
                layout = Grid(12, 12).resolve({
                    "target": target,
                    "neighbor": factory("target", width=2, height=2, gap=1, align=align),
                })
                self.assertEqual(layout.rects["neighbor"], expected)

    def test_centering_uses_upper_left_cell_on_odd_remainder(self):
        layout = Grid(9, 9, allow_overlap=True).resolve({
            "outer": at(1, 1, 5, 5),
            "inner": center_in("outer", width=2, height=2),
            "grid_center": center_in(width=2, height=2),
        })
        self.assertEqual(layout.rects["inner"], Rect(2, 2, 2, 2))
        self.assertEqual(layout.rects["grid_center"], Rect(3, 3, 2, 2))

    def test_unknown_target_cycle_bounds_and_collision(self):
        cases = [
            ({"a": below("missing", width=1, height=1)}, UnknownTargetError),
            ({"a": below("a", width=1, height=1)}, DependencyCycleError),
            ({"a": below("b", width=1, height=1),
              "b": below("a", width=1, height=1)}, DependencyCycleError),
            ({"a": at(-1, 0, 1, 1)}, BoundsError),
            ({"a": at(3, 3, 2, 2)}, BoundsError),
            ({"a": at(0, 0, 2, 2), "b": at(1, 1, 2, 2)}, CollisionError),
        ]
        for placements, error in cases:
            with self.subTest(error=error), self.assertRaises(error):
                Grid(4, 4).resolve(placements)
        self.assertEqual(len(Grid(4, 4, allow_overlap=True).resolve({
            "a": at(0, 0, 2, 2), "b": at(1, 1, 2, 2),
        }).rects), 2)

    def test_square_scaling_and_responsive_selection(self):
        self.assertEqual(Grid(12, 8).unit(1200, 640), 80)
        self.assertEqual(Grid(12, 8).unit(600, 800), 50)
        point = Breakpoint(800, 12, 8, {"card": at(4, 0, 8, 3)})
        grid = Grid(4, 8, mode="reflow", breakpoints=(point,))
        base = {"card": at(0, 0, 4, 3)}
        self.assertEqual(grid.resolve(base, container_width=799).columns, 4)
        self.assertEqual(grid.resolve(base, container_width=800).rects["card"], Rect(4, 0, 8, 3))
        self.assertEqual(grid.unit(1200, 20), 100)
        self.assertEqual(grid.unit(400, 20), 100)
        self.assertEqual(grid.unit(0, 0), 0)

    def test_breakpoints_are_complete_overrides_not_cumulative(self):
        original = {"a": at(0, 0, 1, 1)}
        overrides = {"a": at(1, 0, 1, 1)}
        first = Breakpoint(400, 4, 4, overrides)
        overrides["a"] = at(2, 0, 1, 1)
        grid = Grid(2, 2, mode="reflow", breakpoints=(first, Breakpoint(800, 8, 8)))
        self.assertEqual(grid.resolve(original, container_width=400).rects["a"].x, 1)
        self.assertEqual(grid.resolve(original, container_width=800).rects["a"].x, 0)
        with self.assertRaises(UnknownTargetError):
            grid.resolve({}, container_width=400)

    def test_invalid_grid_and_relationships(self):
        factories = [
            lambda: Grid(0, 1), lambda: Grid(1, 1, mode="world"),
            lambda: Grid(1, 1, breakpoints=(Breakpoint(400, 1, 1),)),
            lambda: Grid(1, 1, mode="reflow", breakpoints=(Breakpoint(400, 1, 1),) * 2),
            lambda: right_of("a", width=1, height=1, gap=-1),
            lambda: below("a", width=1, height=1, align="top"),
            lambda: center_in("", width=1, height=1),
        ]
        for factory in factories:
            with self.subTest(factory=factory), self.assertRaises(ValueError):
                factory()
        for value in (-1, math.inf, math.nan):
            with self.assertRaises(ValueError):
                Grid(1, 1).unit(value, 1)
        with self.assertRaises(TypeError):
            Grid(1, 1).unit(True, 1)
        with self.assertRaises(TypeError):
            Grid(1, 1).resolve({"a": "1,2"})


if __name__ == "__main__":
    unittest.main()
