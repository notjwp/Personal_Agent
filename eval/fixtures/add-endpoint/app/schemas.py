"""Serialisation and validation helpers for item payloads."""

MAX_NAME = 60


class SchemaError(ValueError):
    """Raised when an incoming payload does not match the item schema."""


def to_json(item: dict) -> dict:
    return {"id": item["id"], "name": item["name"], "quantity": item["quantity"]}


def from_json(payload: object) -> tuple[str, int]:
    """Validate an incoming payload and return (name, quantity)."""
    if not isinstance(payload, dict):
        raise SchemaError("body must be a JSON object")

    name = payload.get("name")
    if not isinstance(name, str) or not name.strip():
        raise SchemaError("name must be a non-empty string")
    if len(name) > MAX_NAME:
        raise SchemaError(f"name longer than {MAX_NAME} characters")

    quantity = payload.get("quantity", 1)
    if not isinstance(quantity, int) or isinstance(quantity, bool):
        raise SchemaError("quantity must be an integer")
    if quantity < 0:
        raise SchemaError("quantity must not be negative")

    return name.strip(), quantity
