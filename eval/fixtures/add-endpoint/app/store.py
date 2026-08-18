"""In-memory item storage. Not persisted; recreated per app instance."""


class ItemStore:
    def __init__(self) -> None:
        self._items: dict[int, dict] = {}
        self._next_id = 1

    def next_id(self) -> int:
        return self._next_id

    def list(self) -> list[dict]:
        return [self._items[k] for k in sorted(self._items)]

    def get(self, item_id: int) -> dict | None:
        return self._items.get(item_id)

    def add(self, name: str, quantity: int) -> dict:
        item = {"id": self._next_id, "name": name, "quantity": quantity}
        self._items[self._next_id] = item
        self._next_id += 1
        return item

    def clear(self) -> None:
        self._items.clear()
        self._next_id = 1
