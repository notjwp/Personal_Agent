from datetime import datetime, timedelta, timezone

from scheduler.models import Event
from scheduler.window import events_within, is_within

UTC = timezone.utc


def test_an_event_inside_a_utc_window_is_found():
    event = Event.at("standup", "2026-03-10T12:30:00+00:00")
    start = datetime(2026, 3, 10, 12, 0, tzinfo=UTC)
    end = datetime(2026, 3, 10, 13, 0, tzinfo=UTC)
    assert is_within(event, start, end)


def test_an_offset_timestamp_is_compared_as_the_same_instant():
    """18:00 at +05:30 IS 12:30 UTC, so it falls in a 12:00-13:00 UTC window.

    Dropping the offset and comparing wall-clock numbers puts it at 18:00 and
    outside the window. Same instant, different answer.
    """
    event = Event.at("sync", "2026-03-10T18:00:00+05:30")
    start = datetime(2026, 3, 10, 12, 0, tzinfo=UTC)
    end = datetime(2026, 3, 10, 13, 0, tzinfo=UTC)
    assert is_within(event, start, end)


def test_events_within_filters_the_list():
    inside = Event.at("a", "2026-03-10T12:30:00+00:00")
    outside = Event.at("b", "2026-03-11T12:30:00+00:00")
    start = datetime(2026, 3, 10, 12, 0, tzinfo=UTC)
    end = datetime(2026, 3, 10, 13, 0, tzinfo=UTC)
    assert [e.name for e in events_within([inside, outside], start, end)] == ["a"]
