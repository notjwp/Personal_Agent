"""Core product type."""
from dataclasses import dataclass


@dataclass(frozen=True)
class Product:
    name: str
    score: int
    stock: int = 1
    weight: float = 0.5
