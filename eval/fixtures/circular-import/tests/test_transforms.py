from geometry.models import Point
from geometry.transforms import scale_point


def test_scale_point_multiplies_both_axes():
    assert scale_point(Point(2, 3), 2) == Point(4, 6)


def test_scale_by_one_is_identity():
    assert scale_point(Point(5, 7), 1) == Point(5, 7)
