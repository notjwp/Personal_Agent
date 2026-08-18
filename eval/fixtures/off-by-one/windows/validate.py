"""Argument checks shared by the window helpers."""


def check_size(seq, size: int) -> None:
    if not isinstance(size, int) or isinstance(size, bool):
        raise TypeError("size must be an integer")
    if size < 1:
        raise ValueError("size must be at least 1")
    if size > len(seq):
        raise ValueError(f"size {size} exceeds sequence length {len(seq)}")
