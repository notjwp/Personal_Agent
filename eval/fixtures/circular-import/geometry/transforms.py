"""Moving and resizing shapes."""
from .models import Point
from .shapes import Circle


def translate(shape, dx: float, dy: float):
    """Shift a shape by (dx, dy)."""
    if isinstance(shape, Circle):
        return Circle(shape.centre.moved(dx, dy), shape.radius)
    return type(shape)(shape.corner.moved(dx, dy), shape.size)


def scale_point(point: Point, factor: float) -> Point:
    return Point(point.x * factor, point.y * factor)
