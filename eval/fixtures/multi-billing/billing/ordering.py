"""ordering helpers."""


def by_value(pairs):
    return sorted(pairs, key=lambda p: str(p[1]), reverse=True)
