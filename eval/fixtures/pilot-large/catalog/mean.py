"""Helper: mean."""


def mean(numbers) -> float:
    return sum(numbers) / len(numbers) if numbers else 0.0
