from settings.loader import load_int, load_with_default
from settings.store import SettingsStore


def test_load_with_default_returns_the_stored_value():
    store = SettingsStore({"timeout": 30})
    assert load_with_default(store, "timeout", 5) == 30


def test_load_with_default_falls_back_when_missing():
    """A key that was never set must produce the default, not an error."""
    assert load_with_default(SettingsStore(), "timeout", 5) == 5


def test_load_int_coerces_and_falls_back():
    assert load_int(SettingsStore({"port": "8080"}), "port") == 8080
    assert load_int(SettingsStore(), "port", 80) == 80
