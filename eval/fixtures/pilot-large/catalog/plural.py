"""Helper: plural."""


def plural(word: str, n: int) -> str:
    return word if n == 1 else word + "s"
