"""Core task type."""
from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class Task:
    """One queued item of work."""

    name: str
    due: date
    priority: int = 5

    @property
    def is_urgent(self) -> bool:
        return self.priority <= 2
