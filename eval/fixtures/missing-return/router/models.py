"""Route type."""
from dataclasses import dataclass


@dataclass(frozen=True)
class Route:
    pattern: str
    handler: str

    @property
    def depth(self) -> int:
        return len([p for p in self.pattern.split("/") if p])
