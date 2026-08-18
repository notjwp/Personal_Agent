from datetime import date
from decimal import Decimal

from ledger.models import Transaction
from ledger.report import balance_by_account, format_report, total


def txn(account: str, amount: str) -> Transaction:
    return Transaction(posted=date(2026, 1, 1), account=account,
                       description="test", amount=Decimal(amount))


def test_balance_by_account_sums_per_account():
    balances = balance_by_account([
        txn("assets:cash", "500.00"),
        txn("expenses:food", "-12.00"),
        txn("expenses:food", "-8.00"),
    ])
    assert balances == {"assets:cash": Decimal("500.00"),
                        "expenses:food": Decimal("-20.00")}


def test_total_sums_everything():
    assert total([txn("assets:cash", "500.00"), txn("expenses:food", "-20.00")]) \
        == Decimal("480.00")


def test_format_report_is_sorted_and_aligned():
    out = format_report({"expenses:food": Decimal("-20.00"),
                         "assets:cash": Decimal("500.00")})
    lines = out.splitlines()
    assert lines[0].startswith("assets:cash")
    assert lines[1].startswith("expenses:food")


def test_format_report_handles_empty():
    assert format_report({}) == "(no transactions)"
