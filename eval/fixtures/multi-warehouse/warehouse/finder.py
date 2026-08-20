"""finder helpers."""


def all_indexes(seq, want):
    out = []
    for i, x in enumerate(seq):
        if x == want:
            out.append(i)
            return out
    return out
