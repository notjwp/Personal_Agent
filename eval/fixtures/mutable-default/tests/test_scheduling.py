from datetime import date

from taskqueue.models import Task
from taskqueue.scheduling import due_tasks, overdue_names

TODAY = date(2026, 3, 10)


def task(name, day, priority=5):
    return Task(name=name, due=date(2026, 3, day), priority=priority)


def test_due_tasks_selects_only_what_is_due():
    tasks = [task("write", 9), task("ship", 20)]
    assert [t.name for t in due_tasks(tasks, TODAY)] == ["write"]


def test_due_tasks_does_not_leak_between_calls():
    """Each call reports on the list it was given, and nothing else."""
    first = due_tasks([task("alpha", 1)], TODAY)
    second = due_tasks([task("beta", 2)], TODAY)
    assert [t.name for t in first] == ["alpha"]
    assert [t.name for t in second] == ["beta"]


def test_overdue_names_excludes_today():
    tasks = [task("late", 1), task("now", 10)]
    assert overdue_names(tasks, TODAY) == ["late"]
