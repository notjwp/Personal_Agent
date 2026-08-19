from datetime import datetime, timezone

from scheduler.recurring import every_n_days, weekdays_only

UTC = timezone.utc


def test_every_n_days_steps_by_n():
    out = every_n_days(datetime(2026, 3, 2, tzinfo=UTC), 2, 3)
    assert [d.day for d in out] == [2, 4, 6]


def test_weekdays_only_drops_the_weekend():
    days = every_n_days(datetime(2026, 3, 6, tzinfo=UTC), 1, 4)
    assert [d.day for d in weekdays_only(days)] == [6, 9]
