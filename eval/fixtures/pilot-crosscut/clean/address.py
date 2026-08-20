"""Address fields."""
from .core import normalise


def clean_postcode(value: str) -> str:
    return normalise(value)


def clean_name(value: str) -> str:
    return normalise(value)
