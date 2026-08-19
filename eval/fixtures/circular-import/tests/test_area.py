import math

from geometry.area import circle_area, rect_area
from geometry.models import Size


def test_rect_area_multiplies_sides():
    assert rect_area(Size(3, 4)) == 12


def test_circle_area_uses_pi_r_squared():
    assert math.isclose(circle_area(2), math.pi * 4)
