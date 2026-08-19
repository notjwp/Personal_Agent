from leaderboard.models import Entry
from leaderboard.ranking import ranked, top_n


def test_ranked_puts_the_highest_score_first():
    """10 beats 9. Comparing the numbers as text would put 9 first."""
    out = ranked([Entry("ana", 9), Entry("bo", 10)])
    assert [e.name for e in out] == ["bo", "ana"]


def test_top_n_truncates():
    entries = [Entry("a", 1), Entry("b", 2), Entry("c", 3)]
    assert len(top_n(entries, 2)) == 2
