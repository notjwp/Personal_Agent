from leaderboard.models import Entry
from leaderboard.stats import scoring_players, total_score


def test_total_score_adds_every_entry():
    assert total_score([Entry("a", 3), Entry("b", 4)]) == 7


def test_scoring_players_excludes_zero():
    assert scoring_players([Entry("a", 1), Entry("b", 0)]) == ["a"]
