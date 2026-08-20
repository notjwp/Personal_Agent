import pytest

from clean.core import normalise


def test_each_kind_normalises_differently():
    assert normalise("A B", "name") == "A B"
    assert normalise("A B", "slug") == "a-b"


def test_an_unknown_kind_is_rejected():
    with pytest.raises(ValueError):
        normalise("x", "nope")
