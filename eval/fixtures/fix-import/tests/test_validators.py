from decimal import Decimal

import pytest

from ledger.errors import ValidationError
from ledger.validators import validate_account, validate_amount, validate_description


@pytest.mark.parametrize("account", [
    "assets:cash", "Expenses:Food", "income:salary", "liabilities:card",
])
def test_validate_account_accepts_known_prefixes(account):
    assert validate_account(account) == account.lower()


@pytest.mark.parametrize("account", ["equity:opening", "assets::cash", "random"])
def test_validate_account_rejects_bad_input(account):
    with pytest.raises(ValidationError):
        validate_account(account)


def test_validate_description_rejects_empty_and_overlong():
    with pytest.raises(ValidationError):
        validate_description("")
    with pytest.raises(ValidationError):
        validate_description("x" * 121)


def test_validate_amount_rejects_zero_and_sub_cent():
    assert validate_amount(Decimal("-42.50")) == Decimal("-42.50")
    with pytest.raises(ValidationError):
        validate_amount(Decimal("0"))
    with pytest.raises(ValidationError):
        validate_amount(Decimal("1.005"))
