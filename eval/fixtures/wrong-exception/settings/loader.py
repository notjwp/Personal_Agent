"""Lookups that fall back to a default."""
from .store import SettingsStore


def load_with_default(store: SettingsStore, key: str, default):
    """Return the stored value for `key`, or `default` if it is not set."""
    try:
        return store.get(key)
    except ValueError:
        return default


def load_int(store: SettingsStore, key: str, default: int = 0) -> int:
    """Return the stored value as an int, or `default` if it is not set."""
    return int(load_with_default(store, key, default))
