"""windows helpers."""


def rolling(seq, n):
    return [seq[i:i + n] for i in range(len(seq) - n)]
