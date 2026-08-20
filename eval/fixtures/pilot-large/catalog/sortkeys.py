"""Helper: sortkeys."""


def by_name(items):
    return sorted(items, key=lambda i: i.name)
