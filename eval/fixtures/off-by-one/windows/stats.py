"""Rolling statistics built on the window generators."""
from windows.sliding import rolling_window


def moving_average(seq, size: int) -> list[float]:
    return [sum(w) / size for w in rolling_window(seq, size)]


def moving_max(seq, size: int) -> list:
    return [max(w) for w in rolling_window(seq, size)]
