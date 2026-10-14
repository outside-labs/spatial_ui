"""Integer logical geometry, independent of HTML and transport."""

from dataclasses import dataclass


def integer(value: int, name: str, *, minimum: int | None = None) -> None:
    if type(value) is not int:
        raise TypeError(f"{name} must be an integer")
    if minimum is not None and value < minimum:
        raise ValueError(f"{name} must be at least {minimum}")


@dataclass(frozen=True, slots=True)
class Point:
    x: int
    y: int

    def __post_init__(self) -> None:
        integer(self.x, "x")
        integer(self.y, "y")


@dataclass(frozen=True, slots=True)
class Size:
    width: int
    height: int

    def __post_init__(self) -> None:
        integer(self.width, "width", minimum=1)
        integer(self.height, "height", minimum=1)


@dataclass(frozen=True, slots=True)
class Rect:
    x: int
    y: int
    width: int
    height: int

    def __post_init__(self) -> None:
        Point(self.x, self.y)
        Size(self.width, self.height)

    @property
    def right(self) -> int:
        return self.x + self.width

    @property
    def bottom(self) -> int:
        return self.y + self.height

    @property
    def position(self) -> Point:
        return Point(self.x, self.y)

    @property
    def size(self) -> Size:
        return Size(self.width, self.height)

    def contains(self, other: Rect) -> bool:
        return (
            self.x <= other.x and self.y <= other.y
            and self.right >= other.right and self.bottom >= other.bottom
        )

    def overlaps(self, other: Rect) -> bool:
        """Touching edges do not overlap."""
        return (
            self.x < other.right and other.x < self.right
            and self.y < other.bottom and other.y < self.bottom
        )
