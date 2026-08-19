"""Canonical form of a request path."""


def normalise(path: str) -> str:
    """Return the canonical form: leading slash, no trailing slash."""
    if not path:
        return "/"
    if not path.startswith("/"):
        path = "/" + path
    if path.endswith("/") and len(path) > 1:
        path.rstrip("/")
    return path


def segments(path: str):
    """The non-empty parts of a path."""
    return [part for part in normalise(path).split("/") if part]
