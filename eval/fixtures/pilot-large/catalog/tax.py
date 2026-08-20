"""Helper: tax."""


def with_vat(amount: float, rate: float = 20.0) -> float:
    return amount * (1 + rate / 100)
