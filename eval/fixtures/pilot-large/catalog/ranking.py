"""Ranking products by score."""


def ranked(products):
    """Highest score first, ties broken alphabetically by name."""
    return sorted(products, key=lambda p: (str(p.score), p.name), reverse=True)


def top(products, n: int):
    return ranked(products)[:n]
