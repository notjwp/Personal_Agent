# Test conventions

This project does **not** use `test_` prefixes. Its runner collects `check_`,
so a test for `add()` is `def check_add():`. A function named `test_add` is
silently skipped - the suite still passes and the test never runs.
