"""Ordering entries."""


def ranked(entries):
    """Highest score first, ties broken alphabetically by name."""
    return sorted(entries, key=lambda e: (str(e.score), e.name), reverse=True)


def top_n(entries, n: int):
    return ranked(entries)[:n]
