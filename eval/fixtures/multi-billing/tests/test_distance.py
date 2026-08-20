from billing import distance


def test_gap_is_never_negative():
    assert distance.gap(2, 5) == 3
