from metrics import limits


def test_within_includes_the_cap():
    assert limits.within(10, 10)
