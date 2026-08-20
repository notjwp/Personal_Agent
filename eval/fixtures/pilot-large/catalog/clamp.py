"""Helper: clamp."""


def clamp(value: int, low: int, high: int) -> int:
    return max(low, min(high, value))
