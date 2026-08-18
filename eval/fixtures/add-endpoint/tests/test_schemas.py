"""The payload validator already works too."""
import pytest

from app.schemas import SchemaError, from_json, to_json


def test_from_json_accepts_a_valid_payload():
    assert from_json({"name": "nails", "quantity": 10}) == ("nails", 10)


def test_from_json_defaults_quantity_to_one():
    assert from_json({"name": "nails"}) == ("nails", 1)


@pytest.mark.parametrize("bad", [
    [], {"name": ""}, {"name": "x" * 61}, {"name": "n", "quantity": "3"},
    {"name": "n", "quantity": -1},
])
def test_from_json_rejects_bad_payloads(bad):
    with pytest.raises(SchemaError):
        from_json(bad)


def test_to_json_round_trips():
    assert to_json({"id": 1, "name": "nails", "quantity": 10}) == {
        "id": 1, "name": "nails", "quantity": 10}
