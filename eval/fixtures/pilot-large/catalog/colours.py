"""Helper: colours."""


def hex_to_rgb(value: str):
    v = value.lstrip("#")
    return tuple(int(v[i:i + 2], 16) for i in (0, 2, 4))
