"""Base prices and currency conversion.

Conversion is hot on listing pages, so `converted` is memoised.
"""
from functools import lru_cache


class Catalogue:
    """Base prices in the home currency, converted on demand."""

    def __init__(self, base_prices: dict) -> None:
        self._base = dict(base_prices)
        self._rate = 1.0

    def set_rate(self, rate: float) -> None:
        """Set the conversion rate applied to every price."""
        self._rate = rate

    @lru_cache(maxsize=256)
    def converted(self, sku: str) -> float:
        """Price of `sku` in the target currency."""
        return self._base[sku] * self._rate

    def skus(self):
        return sorted(self._base)
