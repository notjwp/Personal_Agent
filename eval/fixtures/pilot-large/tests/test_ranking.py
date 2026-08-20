from catalog.models import Product
from catalog.ranking import ranked, top


def test_ranked_puts_the_highest_score_first():
    """10 beats 9. Comparing the numbers as text would put 9 first."""
    out = ranked([Product("ana", 9), Product("bo", 10)])
    assert [p.name for p in out] == ["bo", "ana"]


def test_top_truncates():
    items = [Product("a", 1), Product("b", 2), Product("c", 3)]
    assert len(top(items, 2)) == 2
