"""Helper: initials."""


def initials(name: str) -> str:
    return "".join(p[0].upper() for p in name.split())
