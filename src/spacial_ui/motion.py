"""Initial spatial transition settings; browser layout supplies the rectangles."""

from dataclasses import dataclass

from spacial_ui.geometry import integer


@dataclass(frozen=True, slots=True)
class Transition:
    duration: int = 260
    easing: str = "ease-out"

    def __post_init__(self) -> None:
        integer(self.duration, "duration", minimum=0)
        if self.duration > 2000:
            raise ValueError("transition duration must not exceed 2000 milliseconds")
        if self.easing not in {"linear", "ease", "ease-in", "ease-out", "ease-in-out"}:
            raise ValueError("unsupported transition easing")
