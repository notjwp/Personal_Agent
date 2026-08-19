"""Rendering money for display."""


def as_money(amount: float) -> str:
    return f"${amount:,.2f}"


def as_line(description: str, amount: float) -> str:
    return f"{description:<20}{as_money(amount):>12}"
