"""Summary numbers over a leaderboard."""


def total_score(entries) -> int:
    return sum(e.score for e in entries)


def scoring_players(entries):
    return [e.name for e in entries if e.is_scoring]
