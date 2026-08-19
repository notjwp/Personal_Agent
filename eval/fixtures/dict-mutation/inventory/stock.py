"""Applying adjustments to stock levels."""


def _drop_if_empty(levels: dict, sku: str) -> None:
    """Remove a SKU that has run out. Nothing is kept at zero."""
    if levels.get(sku) == 0:
        del levels[sku]


def apply_adjustments(levels: dict, adjustments: dict) -> dict:
    """Apply every adjustment, dropping any SKU that reaches zero."""
    for sku in levels:
        levels[sku] = levels[sku] + adjustments.get(sku, 0)
        _drop_if_empty(levels, sku)
    return levels


def total_units(levels: dict) -> int:
    return sum(levels.values())
