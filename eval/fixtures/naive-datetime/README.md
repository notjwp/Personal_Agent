# scheduler

Calendar events and time-window queries. All stored times are UTC.

## Layout

- `scheduler/models.py` - the Event type and its parser
- `scheduler/window.py` - whether an event falls in a window
- `scheduler/recurring.py` - repeat-rule helpers

## Tests

    pytest -q
