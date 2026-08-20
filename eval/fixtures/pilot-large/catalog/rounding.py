"""Helper: rounding."""


def to_nearest(value: int, step: int) -> int:
    return round(value / step) * step
