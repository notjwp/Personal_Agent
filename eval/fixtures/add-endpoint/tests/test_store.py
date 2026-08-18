"""The storage layer already works; only the HTTP wiring is in question."""
from app.store import ItemStore


def test_add_assigns_incrementing_ids():
    store = ItemStore()
    first = store.add("nails", 10)
    second = store.add("screws", 5)
    assert first["id"] == 1 and second["id"] == 2
    assert store.next_id() == 3


def test_get_returns_none_for_missing():
    assert ItemStore().get(99) is None


def test_list_is_sorted_by_id():
    store = ItemStore()
    store.add("b", 1)
    store.add("a", 1)
    assert [i["id"] for i in store.list()] == [1, 2]
