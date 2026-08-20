"""Helper: currency."""


def to_minor(amount: float) -> int:
    return round(amount * 100)
