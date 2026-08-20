"""Helper: counts."""


def tally(items) -> dict:
    out = {}
    for x in items:
        out[x] = out.get(x, 0) + 1
    return out
