from datetime import date
from decimal import Decimal

import pytest

from ledger.errors import ParseError
from ledger.parser import parse_line, parse_lines


def test_parse_line_builds_a_transaction():
    txn = parse_line("2026-01-15 | expenses:food | Groceries | -42.50")
    assert txn.posted == date(2026, 1, 15)
    assert txn.account == "expenses:food"
    assert txn.amount == Decimal("-42.50")
    assert txn.is_debit


def test_parse_lines_skips_blanks_and_comments():
    txns = parse_lines([
        "# opening balances",
        "",
        "2026-01-01 | assets:cash   | Opening | 500.00",
        "2026-01-02 | expenses:food | Lunch   | -12.00",
    ])
    assert len(txns) == 2


@pytest.mark.parametrize("bad", [
    "2026-01-15 | expenses:food | Groceries",
    "not-a-date | expenses:food | Groceries | -1.00",
    "2026-01-15 | expenses:food | Groceries | abc",
])
def test_parse_line_rejects_malformed_input(bad):
    with pytest.raises(ParseError):
        parse_line(bad)
