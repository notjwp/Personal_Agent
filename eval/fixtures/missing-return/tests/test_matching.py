from router.matching import deepest, find
from router.models import Route


def routes():
    return [Route("/api", "a"), Route("/api/v1/users", "b")]


def test_find_returns_an_exact_match():
    assert find(routes(), "/api").handler == "a"


def test_find_returns_none_when_absent():
    assert find(routes(), "/nope") is None


def test_deepest_picks_the_longest_pattern():
    assert deepest(routes()).handler == "b"
