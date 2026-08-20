from warehouse import shared


def test_label_formats():
    assert shared.label("a", 2) == "a (2)"


def test_is_blank():
    assert shared.is_blank("  ")
