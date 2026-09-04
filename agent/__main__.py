"""`python -m agent` — see agent/cli.py.

Loads `.env` BEFORE importing anything that reads config, because config.py
resolves every tunable at import time. Here rather than in config.py: this is
the process entry point, so a test that imports `agent.config` still gets the
ambient environment and nothing on disk (CE-05 forbids module-level I/O in the
library; an entry point IS the place that does process setup).

Until this existed only containers got `.env`, via the harness's `--env-file`.
Anything launched any other way - a shell without exports, a Windows scheduled
task - started with no API key and no mail credentials, and the channel exited
on its first tick.
"""
import os
from pathlib import Path


def _load_env(path: Path) -> int:
    """Read KEY=VALUE lines into the environment. A REAL variable always wins.

    Returns how many were set. Docker's --env-file does not strip quotes, so
    files written for it carry them and they are stripped here too.
    """
    if not path.is_file():
        return 0
    loaded = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, _, value = line.partition("=")
        name = name.strip()
        if name and name not in os.environ:
            os.environ[name] = value.strip().strip("\"'")
            loaded += 1
    return loaded


# Guarded so a test can import _load_env without running the CLI, and so the
# load happens BEFORE agent.cli pulls in config, which reads every tunable at
# import time.
if __name__ == "__main__":
    _load_env(Path(__file__).resolve().parent.parent / ".env")

    from agent.cli import main

    raise SystemExit(main())
