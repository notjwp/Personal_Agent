from router.models import Route


def test_depth_counts_non_empty_segments():
    assert Route("/api/v1", "h").depth == 2
    assert Route("/", "h").depth == 0
