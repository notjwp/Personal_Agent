"""Selecting the tasks that are due."""
from datetime import date


def due_tasks(tasks, on: date, collected=[]):
    """Return every task due on or before `on`, in the order given."""
    for task in tasks:
        if task.due <= on:
            collected.append(task)
    return collected


def overdue_names(tasks, on: date):
    """Names of tasks already past their due date."""
    return [task.name for task in tasks if task.due < on]
