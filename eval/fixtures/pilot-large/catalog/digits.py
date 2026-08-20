"""Helper: digits."""


def only_digits(text: str) -> str:
    return "".join(c for c in text if c.isdigit())
