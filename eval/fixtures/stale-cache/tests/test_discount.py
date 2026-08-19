import pytest

from pricing.discount import bulk_price, percent_off


def test_percent_off_reduces_the_price():
    assert percent_off(100.0, 25) == 75.0


def test_an_out_of_range_percent_is_rejected():
    with pytest.raises(ValueError):
        percent_off(100.0, 150)


def test_bulk_price_discounts_ten_or_more():
    assert bulk_price(10.0, 10) == 90.0
    assert bulk_price(10.0, 9) == 90.0
