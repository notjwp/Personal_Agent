import pytest

from windows.sliding import chunk, pairwise, rolling_window


def test_rolling_window_includes_final_window():
    assert rolling_window([1, 2, 3, 4, 5], 3) == [
        (1, 2, 3), (2, 3, 4), (3, 4, 5)]


def test_rolling_window_of_full_length_returns_one_window():
    assert rolling_window([1, 2, 3], 3) == [(1, 2, 3)]


def test_chunk_keeps_a_short_final_group():
    assert chunk([1, 2, 3, 4, 5], 2) == [(1, 2), (3, 4), (5,)]


def test_pairwise_pairs_adjacent_elements():
    assert pairwise([1, 2, 3]) == [(1, 2), (2, 3)]


@pytest.mark.parametrize("size", [0, -1, 99])
def test_rolling_window_rejects_bad_sizes(size):
    with pytest.raises(ValueError):
        rolling_window([1, 2, 3], size)
