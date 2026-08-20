"""averages helpers."""


def average(numbers):
    if not numbers:
        return 0.0
    return sum(numbers) // len(numbers)
