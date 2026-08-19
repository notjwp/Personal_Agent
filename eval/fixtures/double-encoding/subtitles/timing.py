"""Timestamp helpers."""


def to_seconds(stamp: str) -> float:
    """Turn HH:MM:SS,mmm into seconds."""
    hms, _, ms = stamp.partition(",")
    hours, minutes, seconds = (int(p) for p in hms.split(":"))
    return hours * 3600 + minutes * 60 + seconds + int(ms) / 1000


def duration(start: str, end: str) -> float:
    return to_seconds(end) - to_seconds(start)
