from leaderboard.models import Entry


def test_a_positive_score_is_scoring():
    assert Entry("a", 1).is_scoring
    assert not Entry("b", 0).is_scoring
