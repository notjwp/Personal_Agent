from invoice.models import LineItem
from invoice.totals import average_line_total, invoice_total, largest_line


def items():
    return [LineItem("design", 1, 10.0), LineItem("build", 1, 15.0)]


def test_invoice_total_sums_lines():
    assert invoice_total(items()) == 25.0


def test_average_line_total_keeps_the_fraction():
    """25.00 over two lines averages 12.50, not 12."""
    assert average_line_total(items()) == 12.5


def test_average_of_nothing_is_zero():
    assert average_line_total([]) == 0.0


def test_largest_line_picks_the_biggest():
    assert largest_line(items()).description == "build"
