"""Helper: weights."""


def heaviest(items):
    return max(items, key=lambda i: i.weight, default=None)
