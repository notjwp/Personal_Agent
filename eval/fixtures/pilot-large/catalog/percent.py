"""Helper: percent."""


def apply(value: float, pct: float) -> float:
    return value * (1 + pct / 100)
