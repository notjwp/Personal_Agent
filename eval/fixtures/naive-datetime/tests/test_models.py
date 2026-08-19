from scheduler.models import Event


def test_event_keeps_its_name():
    assert Event.at("standup", "2026-03-10T09:00:00+00:00").name == "standup"


def test_hour_reads_from_the_timestamp():
    assert Event.at("a", "2026-03-10T09:00:00+00:00").hour == 9
