from windows.stats import moving_average, moving_max


def test_moving_average_length():
    # five elements, window of three -> three windows
    assert len(moving_average([1, 2, 3, 4, 5], 3)) == 3


def test_moving_average_values():
    assert moving_average([1, 2, 3, 4], 2) == [1.5, 2.5, 3.5]


def test_moving_max_values():
    assert moving_max([1, 9, 2, 8], 2) == [9, 9, 8]
