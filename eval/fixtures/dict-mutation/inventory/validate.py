"""Adjustment rules."""


class InvalidAdjustment(ValueError):
    """Raised when an adjustment cannot be applied."""


def check_not_negative(levels: dict, adjustments: dict) -> None:
    for sku, delta in adjustments.items():
        if levels.get(sku, 0) + delta < 0:
            raise InvalidAdjustment(f"{sku} would go negative")
