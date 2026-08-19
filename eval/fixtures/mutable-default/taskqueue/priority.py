"""Ordering helpers."""


def by_priority(tasks):
    """Lowest priority number first, ties broken by name."""
    return sorted(tasks, key=lambda t: (t.priority, t.name))


def urgent_only(tasks):
    return [task for task in tasks if task.is_urgent]
