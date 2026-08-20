"""Contact fields."""
from .core import normalise


def clean_email(value: str) -> str:
    return normalise(value)


def clean_phone(value: str) -> str:
    return normalise(value)
