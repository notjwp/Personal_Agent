from inventory.stock import apply_adjustments, total_units


def test_adjustments_are_added():
    out = apply_adjustments({"a": 5, "b": 3}, {"a": 2})
    assert out["a"] == 7
    assert out["b"] == 3


def test_a_sku_reaching_zero_is_dropped():
    """Nothing is kept at zero, even while other SKUs remain."""
    out = apply_adjustments({"a": 2, "b": 4}, {"a": -2})
    assert "a" not in out
    assert out["b"] == 4


def test_total_units_sums_levels():
    assert total_units({"a": 2, "b": 3}) == 5
