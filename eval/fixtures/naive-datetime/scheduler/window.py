"""Time-window queries over events."""


def is_within(event, start, end) -> bool:
    """Whether the event starts inside the window, bounds included."""
    return start <= event.starts_at <= end


def events_within(events, start, end):
    return [e for e in events if is_within(e, start, end)]
