from router.normalise import normalise, segments


def test_empty_path_becomes_root():
    assert normalise("") == "/"


def test_a_leading_slash_is_added():
    assert normalise("api") == "/api"


def test_a_trailing_slash_is_removed():
    """Strings are immutable - stripping one has to be kept."""
    assert normalise("/api/") == "/api"


def test_root_keeps_its_only_slash():
    assert normalise("/") == "/"


def test_segments_ignores_empty_parts():
    assert segments("/api/v1/") == ["api", "v1"]
