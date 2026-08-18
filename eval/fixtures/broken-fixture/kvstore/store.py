"""The store itself."""
from pathlib import Path

from kvstore.backend import FileBackend
from kvstore.serial import decode, encode


class KVStore:
    def __init__(self, path: Path) -> None:
        self._backend = FileBackend(path)
        self._data: dict[str, str] = {}
        self._open = False

    def open(self) -> "KVStore":
        self._data = self._backend.load()
        self._open = True
        return self

    def close(self) -> None:
        self._backend.save(self._data)
        self._open = False

    def _require_open(self) -> None:
        if not self._open:
            raise RuntimeError("store is not open")

    def set(self, key: str, value) -> None:
        self._require_open()
        self._data[key] = encode(value)

    def get(self, key: str, default=None):
        self._require_open()
        blob = self._data.get(key)
        return default if blob is None else decode(blob)

    def delete(self, key: str) -> None:
        self._require_open()
        self._data.pop(key, None)

    def keys(self) -> list[str]:
        self._require_open()
        return sorted(self._data)
