from billing import averages


def test_average_keeps_the_fraction():
    assert averages.average([10, 15]) == 12.5
