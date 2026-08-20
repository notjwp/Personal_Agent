"""Helper: shipping."""


def band(weight: float) -> str:
    return "light" if weight < 1 else "heavy"
