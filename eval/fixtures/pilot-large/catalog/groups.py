"""Helper: groups."""


def by_first_letter(names) -> dict:
    out = {}
    for n in names:
        out.setdefault(n[0], []).append(n)
    return out
