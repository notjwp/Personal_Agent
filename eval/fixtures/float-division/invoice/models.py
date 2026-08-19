"""Line item type."""
from dataclasses import dataclass


@dataclass(frozen=True)
class LineItem:
    description: str
    quantity: int
    unit_price: float

    @property
    def total(self) -> float:
        return self.quantity * self.unit_price
