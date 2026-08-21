"""The one place that knows which tools exist, and what they cost.

§12 deferred this file with a numeric trigger: *"Break-even against hand-written
schemas is five tools; v1 has three. Add at tool six."* Phase L counted five
(four built-ins plus MCP's `fetch`) and correctly did NOT create it. Phase M's
`remember` is the sixth, so the trigger fires.

**What it is NOT is the `@tool` decorator, and that is deliberate.** §13 costed
that machinery at ~25 lines plus ~5 per tool against ~8 per tool written out, which
breaks even somewhere above eight HAND-WRITTEN tools; there are five, because
`fetch`'s schema arrives from the server already in JSON Schema. The stronger
reason is that the descriptions are load-bearing rather than decorative: the text
in `edit_file`'s schema coaches the model on picking a unique snippet, and that
coaching is what took real repositories from 0/9 to 4/7. Deriving a schema from a
signature either loses that or takes it as a decorator argument - at which point
the schema has been written anyway.

So this file exists for the reason the tool count actually created: **three modules
now contribute tools, and something has to own the merge and the budget.**
`check_budget()` in particular was living in `agent/mcp.py`, which was wrong the
moment a second source of tools appeared - it bounds the whole set, not MCP's part
of it.

CE-05: imports are inside the functions, because mcp.py calls back into here and a
module-level import either way would be circular.
"""
from __future__ import annotations

import json

from agent import config


class ToolBudgetExceeded(RuntimeError):
    """The exposed schemas cost more than config.MAX_SCHEMA_CHARS allows.

    Fatal rather than blocked: retrying cannot fix it, and it is a decision to take
    deliberately - by removing a tool or by re-measuring the cap - never by letting
    a run proceed and quietly spending the difference on every turn.
    """


def toolset() -> dict[str, dict]:
    """Every tool available for this run: built-ins, MCP's, and memory's.

    Built-ins win a name collision, deliberately: neither a server nor the memory
    layer may shadow `run_shell` with its own implementation.
    """
    from agent import mcp, memory
    from agent.tools import TOOLS

    return {**mcp.tools(), **memory.tools(), **TOOLS}


def schemas() -> list[dict]:
    """Schemas in a deterministic order - reordering invalidates a prompt cache."""
    return [entry["schema"] for entry in toolset().values()]


def check_budget(against: list[dict] | None = None) -> int:
    """Refuse to start when the exposed schemas cost more than the cap allows.

    Loud, at startup, naming the overrun - never a warning. The schemas are re-sent
    on EVERY request and the measured provider caches nothing, so an overrun is not
    a one-off: it is a tax on every turn of every run, and its symptom is a slow
    cost drift that nobody attributes to the day a tool was added.
    """
    exposed = schemas() if against is None else against
    size = len(json.dumps(exposed))
    if size > config.MAX_SCHEMA_CHARS:
        raise ToolBudgetExceeded(
            f"tool schemas are {size:,} chars against a cap of "
            f"{config.MAX_SCHEMA_CHARS:,} ({len(exposed)} tools). They are re-sent on "
            f"every request, so this is charged per turn. Remove a tool, or "
            f"re-measure the cap against the current provider and raise it "
            f"deliberately in config.py.")
    return size
