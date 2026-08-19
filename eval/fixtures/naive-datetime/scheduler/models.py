"""The Event type.

Times are stored in UTC so that windows from any timezone can be compared.
"""
from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class Event:
    name: str
    starts_at: datetime

    @classmethod
    def at(cls, name: str, iso: str) -> "Event":
        """Build an event from an ISO-8601 timestamp."""
        stamp = datetime.fromisoformat(iso).replace(tzinfo=None)
        return cls(name=name, starts_at=stamp)

    @property
    def hour(self) -> int:
        return self.starts_at.hour
