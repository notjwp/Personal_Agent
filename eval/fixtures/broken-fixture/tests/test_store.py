def test_set_then_get_round_trips(store):
    store.set("colour", "green")
    assert store.get("colour") == "green"


def test_get_returns_default_for_missing(store):
    assert store.get("absent", "fallback") == "fallback"


def test_delete_removes_a_key(store):
    store.set("a", 1)
    store.delete("a")
    assert store.get("a") is None


def test_keys_are_sorted(store):
    store.set("b", 1)
    store.set("a", 2)
    assert store.keys() == ["a", "b"]


def test_values_survive_types(store):
    store.set("num", 42)
    store.set("list", [1, 2])
    assert store.get("num") == 42
    assert store.get("list") == [1, 2]
