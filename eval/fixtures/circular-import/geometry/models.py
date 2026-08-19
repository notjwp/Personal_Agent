"""Basic coordinate types, depended on by everything else."""
from dataclasses import dataclass


@dataclass(frozen=True)
class Point:
    x: float
    y: float

    def moved(self, dx: float, dy: float) -> "Point":
        return Point(self.x + dx, self.y + dy)


@dataclass(frozen=True)
class Size:
    width: float
    height: float

    @property
    def is_square(self) -> bool:
        return self.width == self.height
