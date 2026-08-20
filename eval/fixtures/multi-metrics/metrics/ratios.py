"""ratios helpers."""


def mean_len(items):
    if not items:
        return 0.0
    return sum(len(i) for i in items) / len(items[0])
