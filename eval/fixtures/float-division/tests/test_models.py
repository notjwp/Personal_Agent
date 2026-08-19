from invoice.models import LineItem


def test_total_multiplies_quantity_by_price():
    assert LineItem("widget", 3, 2.5).total == 7.5


def test_a_zero_quantity_line_totals_nothing():
    assert LineItem("free", 0, 9.99).total == 0
