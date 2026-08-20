"""Formatting a summary line."""


def summary(name: str, count: int) -> str:
    return f"{name}: {count} words"


def banner(title: str) -> str:
    """The title in capitals, underlined to the same width."""
    return title.upper() + chr(10) + "=" * len(title)
