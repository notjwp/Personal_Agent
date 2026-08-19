import pytest

from inventory.validate import InvalidAdjustment, check_not_negative


def test_a_valid_adjustment_passes():
    check_not_negative({"a": 5}, {"a": -3})


def test_going_negative_is_rejected():
    with pytest.raises(InvalidAdjustment):
        check_not_negative({"a": 1}, {"a": -5})
