"""Helper: chunks."""


def chunk(items, n: int):
    return [items[i:i + n] for i in range(0, len(items), n)]
