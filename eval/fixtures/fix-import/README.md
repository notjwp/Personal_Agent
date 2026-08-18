# ledger

A small toolkit for parsing plain-text ledger entries and reporting per-account balances.

Line format:

    YYYY-MM-DD | account | description | amount

Blank lines and lines starting with `#` are ignored.

## Layout

- `ledger/parser.py` — text to `Transaction`
- `ledger/models.py` — the `Transaction` type
- `ledger/validators.py` — field rules applied after parsing
- `ledger/report.py` — per-account balances and formatting
- `ledger/errors.py` — exception types

## Tests

    pytest -q
