from registry import paths


def test_tidy_drops_a_trailing_slash():
    assert paths.tidy("/api/") == "/api"
