"""Core data types."""
from dataclasses import dataclass
from datetime import date
from decimal import Decimal


@dataclass(frozen=True)
class Transaction:
    """One posted ledger entry."""

    posted: date
    account: str
    description: str
    amount: Decimal

    @property
    def is_credit(self) -> bool:
        return self.amount > 0

    @property
    def is_debit(self) -> bool:
        return self.amount < 0

    def signed_minor_units(self) -> int:
        """Amount in cents, sign preserved."""
        return int((self.amount * 100).to_integral_value())
