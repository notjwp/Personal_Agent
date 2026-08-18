"""Field-level rules applied after parsing."""
from decimal import Decimal

from .errors import ValidationError

MAX_DESCRIPTION = 120
ALLOWED_PREFIXES = ("assets:", "expenses:", "income:", "liabilities:")


def validate_account(account: str) -> str:
    lowered = account.lower()
    if not lowered.startswith(ALLOWED_PREFIXES):
        raise ValidationError("account", account, "unknown top-level account")
    if "::" in lowered:
        raise ValidationError("account", account, "empty path segment")
    return lowered


def validate_description(description: str) -> str:
    if not description:
        raise ValidationError("description", description, "must not be empty")
    if len(description) > MAX_DESCRIPTION:
        raise ValidationError("description", description,
                              f"longer than {MAX_DESCRIPTION} chars")
    return description


def validate_amount(amount: Decimal) -> Decimal:
    if amount == 0:
        raise ValidationError("amount", amount, "must be non-zero")
    if amount.as_tuple().exponent < -2:
        raise ValidationError("amount", amount, "more than two decimal places")
    return amount
