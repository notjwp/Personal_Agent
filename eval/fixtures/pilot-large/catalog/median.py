"""Helper: median."""


def median(numbers) -> float:
    s = sorted(numbers)
    return s[len(s) // 2] if s else 0.0
