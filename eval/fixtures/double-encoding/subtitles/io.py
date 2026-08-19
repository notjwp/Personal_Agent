"""Reading subtitle files from disk. Files on disk are UTF-8."""


def load_file(path) -> str:
    """Read one subtitle file."""
    with open(path, encoding="ascii") as fh:
        return fh.read()


def load_many(paths):
    """Read several subtitle files, in order."""
    out = []
    for path in paths:
        with open(path, encoding="ascii") as fh:
            out.append(fh.read())
    return out
