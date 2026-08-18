import pytest

from windows.validate import check_size


def test_check_size_accepts_valid():
    check_size([1, 2, 3], 2)


@pytest.mark.parametrize("size", [0, -5])
def test_check_size_rejects_non_positive(size):
    with pytest.raises(ValueError):
        check_size([1, 2, 3], size)


def test_check_size_rejects_oversized():
    with pytest.raises(ValueError):
        check_size([1, 2], 5)


def test_check_size_rejects_non_integer():
    with pytest.raises(TypeError):
        check_size([1, 2, 3], "2")
