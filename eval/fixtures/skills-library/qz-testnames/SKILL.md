---
name: qz-testnames
description: Use when asked to add or rename a test in this project. Covers the naming convention, which is NOT the usual pytest one.
---

# Test naming

This project does **not** use `test_` prefixes. Its runner collects `check_`
instead, configured in `pyproject.toml`.

- A test for `add()` is `def check_add():`
- Run the suite with `pytest -k check_`

A function named `test_add` will be silently skipped - it is not collected, so
the suite still passes and the test never runs. This is the most common mistake
made in this codebase.
