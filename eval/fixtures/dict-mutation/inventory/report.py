"""Low-stock reporting."""


def low_stock(levels: dict, threshold: int = 5):
    """SKUs at or below the threshold, alphabetically."""
    return sorted(sku for sku, n in levels.items() if n <= threshold)


def restock_order(levels: dict, target: int = 10):
    return {sku: target - n for sku, n in levels.items() if n < target}
