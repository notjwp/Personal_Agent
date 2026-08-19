from pricing.format import as_price, as_range


def test_price_has_two_decimals_and_a_separator():
    assert as_price(1234.5) == "$1,234.50"


def test_range_joins_two_prices():
    assert as_range(1.0, 2.0) == "$1.00 - $2.00"
