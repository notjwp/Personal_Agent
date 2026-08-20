"""Helper: paths."""


def join(*parts) -> str:
    return "/".join(p.strip("/") for p in parts if p)
