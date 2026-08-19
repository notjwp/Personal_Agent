"""Rendering prices."""


def as_price(amount: float, symbol: str = chr(36)) -> str:
    return f"{symbol}{amount:,.2f}"


def as_range(low: float, high: float) -> str:
    return f"{as_price(low)} - {as_price(high)}"
