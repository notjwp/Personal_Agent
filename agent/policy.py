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

# The single source of a tool's risk and the single path through classify().
# Built from tools.TOOLS so a new tool cannot be offered unclassified.
RISK: dict[str, str] = {}


def sync() -> dict[str, str]:
    """Refresh the built-in half of RISK from tools.TOOLS.

    Imported inside the function so the dependency stays one-way: policy knows
    about tools, tools knows nothing about policy, and nothing is read at import
    time that a test could not redirect first (CE-05).
    """
    from agent.tools import TOOLS

    RISK.update({name: entry["risk"] for name, entry in TOOLS.items()
                 if "risk" in entry})
    return RISK


sync()

VERDICT_BY_RISK = {"read": "auto", "write": "auto", "destructive": "confirm"}

# Arguments whose value is a filesystem path and must stay inside the
# workspace (FR-302). Named explicitly: guessing by key name would miss one.
PATH_ARGS = ("path", "file", "filename", "filepath", "cwd", "dir", "directory",
             "folder", "source", "destination", "dest", "target", "output")


# Commands allowed while PLANNING (UR-02). An allowlist, not a denylist: the
# planning gate exists to prevent unapproved EDITS, so anything not provably
# read-only is refused.
_READ_ONLY_VERB = re.compile(
    r"^\s*("
    r"ls|cat|head|tail|find|grep|rg|wc|tree|file|stat|nl|du|basename|dirname"
    r"|sed\s+-n"                       # -n prints; without it sed WRITES
    r"|git\s+(status|log|diff|show|ls-files|branch)"
    # Running the test suite is RESEARCH, and refusing it was the single largest
    # defect in the planning phase - the agent planned a fix for a failure it had
    # never observed. Bare `python` stays denied; see eval/CHANGELOG.md Stage 7.
    r"|python\s+-m\s+pytest"
    r"|pytest"
    r")\b")

# Checked BEFORE splitting on the pipe, because `||` contains one. A redirect is
# the whole risk in an otherwise harmless command (`cat x > y`), and a chain lets
# an allowed verb carry an arbitrary one behind it (`ls && rm -rf build`).
_CHAINED = re.compile(r"[>;`]|&&|\|\|")


def _read_only(name: str, args: dict, risk: str) -> bool:
    """True when this call cannot change anything. Fails closed."""
    if risk == "read":
        return True
    if name != "run_shell":
        return False
    command = str(args.get("command", ""))
    if _CHAINED.search(command):
        return False
    return all(_READ_ONLY_VERB.match(segment) for segment in command.split("|"))


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


def risk_of(name: str) -> str | None:
    """A tool's declared risk, or None when nothing has declared one.

    Falls back to tools.TOOLS so a built-in added after import is classifiable
    without anyone remembering to call sync() - which is what NFR-601 actually
    asks for. An undeclared tool returns None and classify() denies it, the same
    fail-closed default register() applies to an unclassified MCP tool.
    """
    if name in RISK:
        return RISK[name]
    from agent.tools import TOOLS

    entry = TOOLS.get(name)
    return entry.get("risk") if entry else None


def classify(name: str, args: dict, autonomous: bool,
             planning: bool = False) -> tuple[str, str]:
    """Return (verdict, reason). verdict is one of auto | confirm | deny.

    `planning` defaults False so every existing caller keeps the behaviour it was
    measured with. Still pure, still no side effects (FR-305): the gate suspends
    and re-executes from its first line, so this function must be safe to run
    twice.
    """
    for key in PATH_ARGS:
        if key in args and not _inside_workspace(str(args[key])):
            return "deny", f"path escapes workspace: {args[key]}"

    risk = risk_of(name)
    if risk is None:
        return "deny", f"unknown tool: {name}"

    # DENY rather than confirm, on purpose. A `confirm` here would stop the human
    # once per read while the agent looks around - which is the opposite of what
    # planning is for, and would train them to approve without looking.
    if planning and not _read_only(name, args, risk):
        return "deny", (f"planning: {name} could change something, and writes are "
                        f"refused until the plan is accepted. Read and search now; "
                        f"do this once the plan is agreed.")

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
