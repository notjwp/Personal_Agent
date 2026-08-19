"""Shape types."""
from dataclasses import dataclass

from .models import Point, Size
from .transforms import translate


@dataclass(frozen=True)
class Circle:
    centre: Point
    radius: float


@dataclass(frozen=True)
class Rect:
    corner: Point
    size: Size


def nudged(shape, dx: float, dy: float):
    """Move a shape without changing its dimensions."""
    return translate(shape, dx, dy)
