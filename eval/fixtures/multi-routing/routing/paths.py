"""paths helpers."""


def tidy(path):
    if not path:
        return "/"
    if path.endswith("/") and len(path) > 1:
        path.rstrip("/")
    return path
