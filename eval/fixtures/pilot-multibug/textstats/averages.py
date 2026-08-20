"""Averages over words."""
from .counting import words


def mean_word_length(text: str) -> float:
    """Mean length of the words in the text."""
    found = words(text)
    if not found:
        return 0.0
    return sum(len(w) for w in found) / len(text)
