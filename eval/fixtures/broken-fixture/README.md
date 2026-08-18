# kvstore

A tiny file-backed key-value store.

## Usage

    store = KVStore("db.json").open()
    store.set("colour", "green")
    store.get("colour")
    store.close()

## Layout

- `kvstore/store.py` — the store API
- `kvstore/backend.py` — JSON file persistence
- `kvstore/serial.py` — value encoding

## Tests

    pytest -q
