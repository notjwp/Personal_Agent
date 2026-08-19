from datetime import date

from taskqueue.models import Task


def test_task_is_urgent_at_priority_two_or_lower():
    assert Task("a", date(2026, 3, 1), 1).is_urgent
    assert Task("b", date(2026, 3, 1), 2).is_urgent
    assert not Task("c", date(2026, 3, 1), 3).is_urgent


def test_task_defaults_to_priority_five():
    assert Task("a", date(2026, 3, 1)).priority == 5


def test_task_is_frozen():
    import dataclasses
    import pytest
    t = Task("a", date(2026, 3, 1))
    with pytest.raises(dataclasses.FrozenInstanceError):
        t.name = "b"
