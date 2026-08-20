"""Helper: flatten."""


def flatten(nested):
    return [x for group in nested for x in group]
