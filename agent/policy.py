"""classify() — the gate's entire decision logic.

NO SIDE EFFECTS (FR-305). The gate node suspends on `interrupt()` and re-executes
from its first line when a run resumes, so anything that logged, counted, or wrote
here would happen twice.

Note `from agent import config` rather than `from agent.config import WORKSPACE`:
the latter binds the value at import time, so a test redirecting the workspace root
would leave this module still pointing at the real one.
"""
import re
from pathlib import Path

from agent import config

# Commands that must never run unreviewed. Matched against run_shell's command
# string; a hit escalates the tool's declared risk to `destructive`.
DANGER = re.compile(
    r"\brm\s+(-\w+\s+)*-\w*[rf]"
    r"|\bgit\s+push\b[^|]*--force"
    r"|\bgit\s+reset\s+--hard\b"
    r"|\bsudo\b"
    r"|\bmkfs(\.\w+)?\b"
    r"|\bdd\s+if="
    r"|\b(shutdown|reboot|halt|poweroff)\b"
    r"|\bchmod\s+-R\s+777\b"
    r"|\bcurl\b[^|]*\|\s*(ba)?sh\b"
)

# The single source of a tool's risk. `run_shell` is `write` here and is escalated
# only by DANGER above — so this map is the live path, not a dead declaration.
#
# MCP tools are added by register() at run start. They are added HERE rather than
# consulted from a second map, because a second source of risk is a second thing
# that can disagree — and the spec's own fix to the v1 skeleton was that RISK must
# be the single path through classify().
RISK = {"read_file": "read", "write_file": "write", "edit_file": "write",
        "run_shell": "write"}

VERDICT_BY_RISK = {"read": "auto", "write": "auto", "destructive": "confirm"}

# Arguments whose value is a filesystem path and must stay inside the workspace.
#
# The first three are the names THIS PROJECT's tools happen to use, and that was
# enough while the tool set was closed. It is not enough once a third party can
# register a tool: a server calling its argument `filename` or `directory` would
# have walked straight past this check. Widened deliberately, and tested for the
# bypass rather than assumed closed.
#
# `url` and `uri` are deliberately NOT here. They are not filesystem paths, and
# running one through the workspace check would resolve "https://x/y" into a
# subdirectory of the workspace and quietly approve it — a check that produces a
# confident wrong answer is worse than no check.
PATH_ARGS = ("path", "file", "filename", "filepath", "cwd", "dir", "directory",
             "folder", "source", "destination", "dest", "target", "output")


def register(name: str, risk: str | None) -> str:
    """Declare an MCP tool's risk before its schema is ever shown to the model.

    Returns the risk actually recorded, which is not always the one requested.

    An unknown or missing risk becomes `destructive`, never `read`. That makes an
    unclassified tool VISIBLE — it pauses for approval interactively and is refused
    unattended — instead of silently trusted. This is the same failure Phase K
    recorded from the other side: `AGENT_EGRESS` defaulted to "restricted", so every
    trace row claimed a condition nobody had checked. A default that asserts the
    safe-looking answer hides exactly what it should surface.
    """
    RISK[name] = risk if risk in VERDICT_BY_RISK else "destructive"
    return RISK[name]


def classify(name: str, args: dict, autonomous: bool) -> tuple[str, str]:
    """Return (verdict, reason). verdict is one of auto | confirm | deny."""
    for key in PATH_ARGS:
        if key in args and not _inside_workspace(str(args[key])):
            return "deny", f"path escapes workspace: {args[key]}"

    risk = RISK.get(name)
    if risk is None:
        return "deny", f"unknown tool: {name}"

    if name == "run_shell" and DANGER.search(str(args.get("command", ""))):
        risk = "destructive"

    verdict = VERDICT_BY_RISK[risk]
    if autonomous and verdict == "confirm":
        return "deny", f"{name} is {risk}; denied in autonomous mode, queued for review"
    return verdict, f"{name} classified {risk}"


def _inside_workspace(value: str) -> bool:
    """True when `value` resolves inside the workspace root. Fails closed.

    `.resolve()` follows symlinks, so a link inside the workspace pointing out
    resolves outside and is rejected.
    """
    root = config.WORKSPACE
    try:
        candidate = Path(value)
        resolved = (candidate if candidate.is_absolute() else root / candidate).resolve()
    except (OSError, ValueError, RuntimeError):
        return False
    return resolved == root or root in resolved.parents
