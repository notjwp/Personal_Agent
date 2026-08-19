# taskqueue

A small task queue: filter tasks by due date, order them by priority.

## Layout

- `taskqueue/models.py` - the `Task` type
- `taskqueue/scheduling.py` - selecting tasks that are due
- `taskqueue/priority.py` - ordering helpers

## Tests

    pytest -q
