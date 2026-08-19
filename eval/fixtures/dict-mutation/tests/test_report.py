from inventory.report import low_stock, restock_order


def test_low_stock_uses_the_threshold():
    assert low_stock({"a": 2, "b": 9}) == ["a"]


def test_low_stock_is_sorted():
    assert low_stock({"z": 1, "a": 1}) == ["a", "z"]


def test_restock_order_tops_up_to_target():
    assert restock_order({"a": 8}, target=10) == {"a": 2}
