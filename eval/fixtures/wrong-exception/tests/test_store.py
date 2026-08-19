import pytest

from settings.store import SettingsStore


def test_get_returns_a_stored_value():
    assert SettingsStore({"a": 1}).get("a") == 1


def test_get_raises_for_a_missing_key():
    with pytest.raises(KeyError):
        SettingsStore().get("nope")


def test_keys_are_sorted():
    assert SettingsStore({"b": 1, "a": 2}).keys() == ["a", "b"]
