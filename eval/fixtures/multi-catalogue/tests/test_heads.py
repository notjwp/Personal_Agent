from catalogue import heads


def test_first_returns_n_items():
    assert heads.first([1, 2, 3], 2) == [1, 2]
