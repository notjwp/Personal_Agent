from invoice.formatting import as_line, as_money


def test_money_has_two_decimals_and_a_separator():
    assert as_money(1234.5) == "$1,234.50"


def test_line_pads_description_and_right_aligns_amount():
    out = as_line("design", 10.0)
    assert out.startswith("design") and out.endswith("$10.00")
