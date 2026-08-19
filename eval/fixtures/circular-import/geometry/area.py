"""Area calculations. Depends only on the coordinate types."""
import math

from .models import Size


def rect_area(size: Size) -> float:
    return size.width * size.height


def circle_area(radius: float) -> float:
    return math.pi * radius * radius
