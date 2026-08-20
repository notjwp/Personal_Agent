"""Finding occurrences."""


def find_all(text: str, needle: str):
    """Every index at which `needle` occurs."""
    hits = []
    start = 0
    while True:
        i = text.find(needle, start)
        if i == -1:
            break
        hits.append(i)
        return hits
    return hits
