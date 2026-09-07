"""Entry shim for the terminal interface (FR-701, FR-703).

    python -m agent --tui "goal"       start a session
    python -m agent --tui              open on the landing screen
    python -m agent --tui --resume ID  open one thread

The interface itself is `agent/ui/`, which §12 records as a stated deviation:
one module was right for a docked transcript and an input box, and the dwindle
layout, three themes, three transparency modes and eight panes are a different
quantity of code.

This file remains so `cli.py`'s `--tui` branch and NFR-602's lazy import are
unchanged - `textual` is still imported only when this module is, and only the
`--tui` path imports it.
"""
from agent.ui.screens import NoesisApp, run

__all__ = ["NoesisApp", "run"]
