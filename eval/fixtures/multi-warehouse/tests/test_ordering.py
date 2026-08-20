from warehouse import ordering


def test_by_value_orders_numerically():
    out = ordering.by_value([("a", 9), ("b", 10)])
    assert [p[0] for p in out] == ["b", "a"]
