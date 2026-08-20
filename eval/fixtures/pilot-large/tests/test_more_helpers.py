from catalog import clamp, dates, digits, plural, tax, truncate


def test_clamp_bounds():
    assert clamp.clamp(15, 0, 10) == 10


def test_plural_respects_one():
    assert plural.plural("box", 1) == "box"


def test_digits_only():
    assert digits.only_digits("a1b2") == "12"


def test_truncate_adds_ellipsis():
    assert truncate.shorten("abcdef", 3) == "abc..."


def test_leap_years():
    assert dates.is_leap(2024) and not dates.is_leap(2100)


def test_vat_default():
    assert tax.with_vat(100.0) == 120.0
