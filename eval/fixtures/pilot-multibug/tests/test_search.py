from textstats.search import find_all


def test_find_all_returns_every_occurrence():
    assert find_all("abab", "ab") == [0, 2]


def test_find_all_returns_empty_when_absent():
    assert find_all("abc", "z") == []
