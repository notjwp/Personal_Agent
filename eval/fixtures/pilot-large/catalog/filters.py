"""Helper: filters."""


def only_available(items):
    return [i for i in items if i.stock > 0]
