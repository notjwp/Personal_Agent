"""Leaderboard entry."""
from dataclasses import dataclass


@dataclass(frozen=True)
class Entry:
    name: str
    score: int

    @property
    def is_scoring(self) -> bool:
        return self.score > 0
