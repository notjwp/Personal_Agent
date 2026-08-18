"""Aggregate transactions into per-account balances."""
from collections import defaultdict
from decimal import Decimal

from .models import Transaction


def balance_by_account(transactions: list[Transaction]) -> dict[str, Decimal]:
    balances: dict[str, Decimal] = defaultdict(Decimal)
    for txn in transactions:
        balances[txn.account] += txn.amount
    return dict(balances)


def total(transactions: list[Transaction]) -> Decimal:
    return sum((t.amount for t in transactions), Decimal("0"))


def format_report(balances: dict[str, Decimal]) -> str:
    if not balances:
        return "(no transactions)"
    width = max(len(a) for a in balances)
    rows = [f"{account:<{width}}  {amount:>12}"
            for account, amount in sorted(balances.items())]
    return "\n".join(rows)
