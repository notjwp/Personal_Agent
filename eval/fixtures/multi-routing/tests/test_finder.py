from routing import finder


def test_all_indexes_finds_every_match():
    assert finder.all_indexes([1, 2, 1], 1) == [0, 2]
