"""Repeat-rule helpers."""
from datetime import timedelta


def every_n_days(start, n: int, count: int):
    """`count` timestamps, `n` days apart, beginning at `start`."""
    return [start + timedelta(days=n * i) for i in range(count)]


def weekdays_only(stamps):
    return [s for s in stamps if s.weekday() < 5]
