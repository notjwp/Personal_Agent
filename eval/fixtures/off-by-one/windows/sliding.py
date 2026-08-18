"""Window generators over a sequence."""
from windows.validate import check_size


def rolling_window(seq, size: int) -> list[tuple]:
    """Every contiguous window of `size` elements, left to right."""
    check_size(seq, size)
    return [tuple(seq[i:i + size]) for i in range(len(seq) - size)]


def chunk(seq, size: int) -> list[tuple]:
    """Non-overlapping groups of `size`; a short final group is kept."""
    check_size(seq, size)
    return [tuple(seq[i:i + size]) for i in range(0, len(seq), size)]


def pairwise(seq) -> list[tuple]:
    """Adjacent pairs: (s0, s1), (s1, s2), ..."""
    return [(seq[i], seq[i + 1]) for i in range(len(seq) - 1)]
