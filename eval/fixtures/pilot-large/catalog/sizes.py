"""Helper: sizes."""


def next_size(size: str) -> str:
    order = ["S", "M", "L"]
    i = order.index(size)
    return order[min(i + 1, len(order) - 1)]
