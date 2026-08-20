from catalog import chunks, currency, mean, slugs, uniq, units


def test_units_convert():
    assert units.to_grams(2) == 2000


def test_currency_to_minor():
    assert currency.to_minor(1.5) == 150


def test_chunking():
    assert chunks.chunk([1, 2, 3], 2) == [[1, 2], [3]]


def test_uniq_preserves_order():
    assert uniq.uniq([1, 2, 1]) == [1, 2]


def test_mean_of_empty_is_zero():
    assert mean.mean([]) == 0.0


def test_slugify():
    assert slugs.slugify("Hello World") == "hello-world"
