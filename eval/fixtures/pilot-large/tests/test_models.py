from catalog.models import Product


def test_product_defaults():
    p = Product("a", 5)
    assert p.stock == 1 and p.weight == 0.5
