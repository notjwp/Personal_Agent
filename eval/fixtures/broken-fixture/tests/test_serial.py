import pytest

from kvstore.serial import decode, encode


def test_encode_decode_round_trips():
    for value in ["text", 42, [1, 2], {"k": "v"}, None, True]:
        assert decode(encode(value)) == value


def test_encode_is_stable():
    assert encode({"b": 1, "a": 2}) == encode({"a": 2, "b": 1})


def test_decode_rejects_empty():
    with pytest.raises(ValueError):
        decode("")
