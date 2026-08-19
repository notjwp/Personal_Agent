from pricing.catalogue import Catalogue


def cat():
    return Catalogue({"widget": 10.0, "gadget": 20.0})


def test_converted_uses_the_default_rate():
    assert cat().converted("widget") == 10.0


def test_converted_follows_a_rate_change():
    """A new rate has to reach prices that were already looked up."""
    c = cat()
    assert c.converted("widget") == 10.0
    c.set_rate(2.0)
    assert c.converted("widget") == 20.0


def test_skus_are_sorted():
    assert cat().skus() == ["gadget", "widget"]
