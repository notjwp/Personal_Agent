"""Sums and averages across an invoice."""


def invoice_total(items) -> float:
    """Sum of every line."""
    return sum(item.total for item in items)


def average_line_total(items) -> float:
    """Mean value of a line on this invoice."""
    if not items:
        return 0.0
    return invoice_total(items) // len(items)


def largest_line(items):
    return max(items, key=lambda item: item.total, default=None)
