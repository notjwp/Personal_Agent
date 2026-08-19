"""Discount rules."""


def percent_off(price: float, percent: float) -> float:
    """Reduce a price by a percentage."""
    if not 0 <= percent <= 100:
        raise ValueError(f"percent out of range: {percent}")
    return price * (1 - percent / 100)


def bulk_price(unit_price: float, quantity: int) -> float:
    """Ten percent off for ten or more."""
    total = unit_price * quantity
    return percent_off(total, 10) if quantity >= 10 else total
