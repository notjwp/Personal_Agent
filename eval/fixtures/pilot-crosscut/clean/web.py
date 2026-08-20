"""Web-facing fields."""
from .core import normalise


def clean_url(value: str) -> str:
    return normalise(value)


def clean_slug(value: str) -> str:
    return normalise(value)
