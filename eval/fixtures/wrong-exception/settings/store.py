"""The key/value store."""


class SettingsStore:
    """An in-memory settings store.

    A missing key raises KeyError, matching dict semantics.
    """

    def __init__(self, values=None):
        self._values = dict(values or {})

    def get(self, key: str):
        if key not in self._values:
            raise KeyError(key)
        return self._values[key]

    def set(self, key: str, value) -> None:
        self._values[key] = value

    def keys(self):
        return sorted(self._values)
