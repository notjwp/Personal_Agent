from subtitles.timing import duration, to_seconds


def test_to_seconds_includes_milliseconds():
    assert to_seconds("00:00:01,500") == 1.5


def test_to_seconds_handles_hours():
    assert to_seconds("01:02:03,000") == 3723


def test_duration_subtracts():
    assert duration("00:00:01,000", "00:00:03,500") == 2.5
