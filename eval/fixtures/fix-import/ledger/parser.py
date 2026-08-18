"""Turn ledger text into Transaction objects.

Line format:  YYYY-MM-DD | account | description | amount
"""
from datetime import date
from decimal import Decimal, InvalidOperation

from ..errors import ParseError
from .models import Transaction

FIELD_SEPARATOR = "|"
EXPECTED_FIELDS = 4


def parse_line(text: str, line_no: int = 1) -> Transaction:
    """Parse a single ledger line into a Transaction."""
    parts = [p.strip() for p in text.split(FIELD_SEPARATOR)]
    if len(parts) != EXPECTED_FIELDS:
        raise ParseError(line_no, text, f"expected {EXPECTED_FIELDS} fields, got {len(parts)}")

    raw_date, account, description, raw_amount = parts
    try:
        posted = date.fromisoformat(raw_date)
    except ValueError:
        raise ParseError(line_no, text, "date is not ISO-8601") from None
    try:
        amount = Decimal(raw_amount)
    except InvalidOperation:
        raise ParseError(line_no, text, "amount is not a decimal") from None

    return Transaction(posted=posted, account=account,
                       description=description, amount=amount)


def parse_lines(lines) -> list[Transaction]:
    """Parse an iterable of lines, skipping blanks and `#` comments."""
    out = []
    for line_no, text in enumerate(lines, start=1):
        stripped = text.strip()
        if not stripped or stripped.startswith("#"):
            continue
        out.append(parse_line(stripped, line_no))
    return out
