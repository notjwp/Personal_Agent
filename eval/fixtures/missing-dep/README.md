# gradebook

Coursework grading: load score sheets, compute averages and letter grades, render a table.

## Layout

- `gradebook/grades.py` — averages and letter bands
- `gradebook/loader.py` — CSV import/export
- `gradebook/render.py` — table rendering

Dependencies are listed in `requirements.txt`.

## Tests

    pytest -q
