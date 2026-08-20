"""Splitting text into words."""


def words(text: str):
    """Every word in the text, ignoring surrounding whitespace."""
    return text.strip().split(" ")


def word_count(text: str) -> int:
    return len(words(text))
