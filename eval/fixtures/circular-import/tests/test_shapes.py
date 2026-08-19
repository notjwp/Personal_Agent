from geometry.models import Point, Size
from geometry.shapes import Circle, Rect, nudged


def test_circle_holds_its_centre():
    c = Circle(Point(1, 2), 3)
    assert c.centre == Point(1, 2) and c.radius == 3


def test_nudged_moves_a_circle():
    moved = nudged(Circle(Point(0, 0), 1), 2, 3)
    assert moved.centre == Point(2, 3)


def test_nudged_moves_a_rect():
    moved = nudged(Rect(Point(0, 0), Size(2, 2)), 1, 1)
    assert moved.corner == Point(1, 1)
