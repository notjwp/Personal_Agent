"""The one place that knows which tools exist, and what they cost.

§12 deferred this file with a numeric trigger: *"Break-even against hand-written
schemas is five tools; v1 has three. Add at tool six."* Phase L counted five
(four built-ins plus MCP's `fetch`) and correctly did NOT create it. Phase M's
`remember` is the sixth, so the trigger fires.

**The `@tool` decorator arrived 2026-08-23, and the arithmetic is why.** §13
costed the machinery at ~25 lines plus ~5 per tool against ~8 per tool written
out, breaking even above eight HAND-WRITTEN schemas. There were five when this
file was created and it correctly said no; `search_files` is the eighth, so the
objection expired. FR-207 is [M] and §0 says a requirement beats existing code,
but it is worth noting the two only agreed once the count moved.

The descriptions are still load-bearing and are NOT derived from parameter names:
they come from the docstring, which now carries the exact text the schema dicts
used to. `edit_file`'s coaching on picking a unique snippet is what took real
repositories from 0/9 to 4/7, and a decorator that emitted `{"type": "string"}`
per argument would have satisfied FR-207 while throwing that away.

So this file exists for the reason the tool count actually created: **three modules
now contribute tools, and something has to own the merge and the budget.**
`check_budget()` in particular was living in `agent/mcp.py`, which was wrong the
moment a second source of tools appeared - it bounds the whole set, not MCP's part
of it.

CE-05: imports are inside the functions, because mcp.py calls back into here and a
module-level import either way would be circular.
"""
from __future__ import annotations

import inspect
import json

from agent import config


class ToolBudgetExceeded(RuntimeError):
    """The exposed schemas cost more than config.MAX_SCHEMA_CHARS allows.

    Fatal rather than blocked: retrying cannot fix it, and it is a decision to take
    deliberately - by removing a tool or by re-measuring the cap - never by letting
    a run proceed and quietly spending the difference on every turn.
    """


# FR-207: the schema is derived from the signature and docstring. What it does
# NOT derive is the prose - descriptions come from the DOCSTRING, which is the
# one piece of prompt text here with a measured effect on the pass rate.
_JSON_TYPES = {str: "string", int: "integer", bool: "boolean", float: "number"}


def _describe(fn) -> tuple[str, dict[str, str]]:
    """Split a docstring into the tool description and its per-parameter lines.

    The convention is one `name: text` line per parameter, after the prose. A
    line only counts when `name` is an actual parameter, so a description
    containing a colon - `Returns path:line: matches` - is not mistaken for one.
    """
    names = set(inspect.signature(fn).parameters)
    prose, params = [], {}
    for line in (inspect.getdoc(fn) or "").splitlines():
        head, sep, tail = line.partition(":")
        if sep and head.strip() in names:
            params[head.strip()] = " ".join(tail.split())
        elif not params:
            prose.append(line)
    return " ".join(" ".join(prose).split()), params


def tool(risk: str):
    """Attach a generated schema to a tool function (FR-207).

    `risk` is the one thing a signature cannot express, so it stays explicit -
    and it is declared here, beside the function, which is what keeps NFR-601
    true: adding a tool touches one file.
    """
    def decorate(fn):
        description, described = _describe(fn)
        properties, required = {}, []
        for name, param in inspect.signature(fn).parameters.items():
            properties[name] = {
                "type": _JSON_TYPES.get(param.annotation, "string"),
                "description": described.get(name, ""),
            }
            if param.default is inspect.Parameter.empty:
                required.append(name)
        fn.spec = {
            "fn": fn,
            "risk": risk,
            "schema": {"name": fn.__name__, "description": description,
                       "input_schema": {"type": "object", "properties": properties,
                                        "required": required}},
        }
        return fn

    return decorate


def toolset() -> dict[str, dict]:
    """Every tool available for this run: built-ins, MCP's, and memory's.

    Built-ins win a name collision, deliberately: neither a server nor the memory
    layer may shadow `run_shell` with its own implementation.
    """
    from agent import mcp, memory, skills
    from agent.tools import builtins

    return {**mcp.tools(), **memory.tools(), **skills.tools(), **builtins()}


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
