from datetime import date

from taskqueue.models import Task
from taskqueue.priority import by_priority, urgent_only


def task(name, priority):
    return Task(name=name, due=date(2026, 3, 10), priority=priority)


def test_by_priority_orders_lowest_number_first():
    out = by_priority([task("b", 5), task("a", 1)])
    assert [t.name for t in out] == ["a", "b"]


def test_by_priority_breaks_ties_by_name():
    out = by_priority([task("z", 3), task("a", 3)])
    assert [t.name for t in out] == ["a", "z"]


def test_urgent_only_keeps_priority_one_and_two():
    out = urgent_only([task("a", 1), task("b", 2), task("c", 3)])
    assert [t.name for t in out] == ["a", "b"]
