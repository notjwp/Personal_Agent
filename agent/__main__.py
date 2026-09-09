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
import sys
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


def _require_workspace(root: Path, argv: list[str]) -> None:
    """Refuse to start when the workspace does not exist. Returns, or exits 2.

    `/workspace` is the CONTAINER's mount point and the right default there. Off
    it - the CLI, the TUI, a scheduled task - `Path("/workspace").resolve()` is a
    drive-relative path that does not exist, and the first tool call died as
    `NotADirectoryError: [WinError 267]`, which names nothing.

    NOT given a fallback on purpose. A default that quietly redirects every write
    to a directory nobody chose is the same defect as AGENT_EGRESS defaulting to
    "restricted": it asserts the safe-looking answer and hides what it did.

    `--doctor` is exempt. It exists to REPORT this, and a guard that blocks the
    diagnostic is worse than the fault it guards against.
    """
    if "--doctor" in argv or root.is_dir():
        return
    print(f"workspace does not exist: {root}\n"
          f"Set AGENT_WORKSPACE to the directory the agent may work in, or "
          f"create that one.\nThe default /workspace is the container's mount "
          f"point.\nRun `python -m agent --doctor` to see every precondition.",
          file=sys.stderr)
    raise SystemExit(2)


# Guarded so a test can import _load_env without running the CLI, and so the
# load happens BEFORE agent.cli pulls in config, which reads every tunable at
# import time.
if __name__ == "__main__":
    _load_env(Path(__file__).resolve().parent.parent / ".env")

    # The wizard runs in the same window .env is loaded in, and for the same
    # reason: config.py resolves PROVIDER, OPENAI_MODEL and OPENAI_BASE_URL at
    # IMPORT time, so a key entered after agent.cli is imported changes nothing.
    from agent import setup

    if setup.needed() and setup.interactive() and setup.run():
        import importlib

        from agent import config

        importlib.reload(config)

    from agent import config

    _require_workspace(config.WORKSPACE, sys.argv[1:])

    from agent.cli import main

    raise SystemExit(main())
