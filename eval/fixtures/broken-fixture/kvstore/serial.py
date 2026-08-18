"""Encoding helpers. Values are stored as JSON with a type tag."""
import json


def encode(value) -> str:
    return json.dumps({"v": value}, sort_keys=True)


def decode(blob: str):
    if not blob:
        raise ValueError("cannot decode an empty blob")
    return json.loads(blob)["v"]
