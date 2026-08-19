from geometry.models import Point, Size


def test_point_moves_by_an_offset():
    assert Point(1, 1).moved(2, 3) == Point(3, 4)


def test_size_knows_when_it_is_square():
    assert Size(2, 2).is_square
    assert not Size(2, 3).is_square
