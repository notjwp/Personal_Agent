"""Single source of truth for every tunable in the system.

FR-302 and NFR-201 both depend on WORKSPACE having exactly one definition, so it
is never re-derived elsewhere - scripts/reset.sh reads the same variable.
"""
import os
from pathlib import Path

# Every AGENT_* name this module reads, recorded as it is read. eval/harness.py
# derives what it forwards into a scored container from this - a hand-kept list
# has silently omitted a variable three times.
ENV_VARS: list = []


def _env(name: str, default: str = "") -> str:
    ENV_VARS.append(name)
    return os.environ.get(name, default)


# --- locations -------------------------------------------------------------

WORKSPACE = Path(_env("AGENT_WORKSPACE", "/workspace")).resolve()

# Artifacts sit INSIDE the workspace so a spilled file is readable without
# tripping FR-302; state sits OUTSIDE it so reset.sh cannot wipe it.
ARTIFACTS = WORKSPACE / ".agent" / "artifacts"
AGENT_HOME = Path(_env("AGENT_HOME", "/state")).resolve()
STATE_DB = AGENT_HOME / "state.db"

# --- model -----------------------------------------------------------------

PROVIDER = _env("AGENT_PROVIDER", "nvidia")

MODEL = "claude-opus-5"
EFFORT = "medium"        # low | medium | high | xhigh | max

# OPENAI_* names the protocol, not the vendor: any compatible endpoint works.
# NIM_* is kept so existing .env files do not break.
OPENAI_BASE_URL = (os.environ.get("OPENAI_BASE_URL")
                   or os.environ.get("NIM_BASE_URL")
                   or "https://integrate.api.nvidia.com/v1")

# Chosen by probe, not reputation - model history is in eval/CHANGELOG.md.
# Changing this invalidates every baseline: it is a different measurement.
OPENAI_MODEL = (os.environ.get("OPENAI_MODEL")
                or os.environ.get("NIM_MODEL")
                or "nvidia/nemotron-3-super-120b-a12b")

MAX_TOKENS = 16_000      # on Anthropic this caps thinking AND response together

# Turn and token caps do not bound a hung request, and the SDK defaults are
# 10 min x 3 attempts - half an hour of silence before anything surfaces.
REQUEST_TIMEOUT = float(_env("AGENT_REQUEST_TIMEOUT", "120"))
MAX_ATTEMPTS = 3


def _clean(value: str | None) -> str:
    """Strip whitespace and surrounding quotes.

    Docker's --env-file does not strip quotes, so KEY="abc" arrives with them and
    the only symptom is an auth failure that looks nothing like a quoting bug.
    """
    value = (value or "").strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        value = value[1:-1]
    return value.strip()


# Checked in order; AGENT_API_KEY is the provider-neutral one to prefer.
KEY_VARS = ("AGENT_API_KEY", "OPENAI_API_KEY", "NVIDIA_API_KEY")


def openai_api_key() -> str:
    """Read at call time, never at import: importing config must not require a key."""
    for name in KEY_VARS:
        key = _clean(os.environ.get(name))
        if key:
            return key
    raise RuntimeError(
        "no API key for the OpenAI-compatible provider. Set AGENT_API_KEY, and "
        "OPENAI_BASE_URL / OPENAI_MODEL for the endpoint you want - any "
        "OpenAI-compatible provider works (NVIDIA NIM, OpenAI, OpenRouter, Groq, "
        "a local Ollama). Or set AGENT_PROVIDER=anthropic with ANTHROPIC_API_KEY.")

# --- budgets ---------------------------------------------------------------

MAX_TURNS = 12
BUDGET_TOKENS = 200_000

# WORKING seconds, accumulated by the nodes that spend time - not wall-clock
# since the goal arrived, or a thread resumed next week dies on its first turn.
#
# RAISED 900 -> 1500 and DERIVED, not chosen. 900 was sized against a hanging
# tool; measured over 21 real-* runs, tools take 9-11s and the model takes the
# rest. At 50.9s/turn (p50) a 900s cap buys 17.7 of a 30-turn allowance, so it
# was ending working runs, not runaway ones - all three of the last baseline
# died at 932-965s with 8-17 turns unused. 1500 is the smallest cap at which a
# median run can reach max_turns; 1800 only helps the slow tail and costs 20%.
MAX_SECONDS = int(_env("AGENT_MAX_SECONDS", "1500"))

# Fires on CONTEXT SIZE, not cumulative spend: spent_tokens only grows, so a
# spend-based trigger would fire every turn forever once crossed. 45,000 is
# where the old trigger effectively fired; derivation in eval/CHANGELOG.md.
COMPACT_AT_CHARS = int(_env("AGENT_COMPACT_AT", "45000"))

# At the cap the run ends `stuck` rather than looping: a compaction loop burns
# budget faster than the problem it solves.
MAX_COMPACTIONS = int(_env("AGENT_MAX_COMPACTIONS", "3"))

# Refuse to finish on an edit that was never verified. DEFAULT OFF, as Hermes
# ships it: our loop already runs to a turn cap, so this may be machinery for
# a problem we do not have. Bounded at two nudges - past that it is nagging.
VERIFY_ON_STOP = _env("AGENT_VERIFY_ON_STOP", "off").strip().lower() not in (
    "0", "off", "false")
MAX_VERIFY_NUDGES = 2

# NFR-101. Stream the OpenAI-compatible reply so there IS a first token to
# measure. Off restores the single-block path exactly, which is the fallback if
# an endpoint streams badly rather than not at all.
STREAM = _env("AGENT_STREAM", "1") not in ("0", "false", "no")

# FR-607. How many tasks may be `running` at once. Each worker holds a
# container and the shared /workspace, so the real bound is the machine, not
# the queue - one is the honest default for a single-user agent.
MAX_WORKERS = int(_env("AGENT_MAX_WORKERS", "1"))

# --- planning (FR-101, FR-105, UR-02) --------------------------------------

# DEFAULT OFF because it does not pay, not because it does not work: 1/3 on
# add-endpoint, stuck at the cap 3/3, 82,435 median tokens against NFR-402's
# 60,000. See eval/CHANGELOG.md.
PLAN_ENABLED = _env("AGENT_PLAN", "off").strip().lower() not in (
    "0", "off", "false")

# Capped SEPARATELY and not charged against MAX_TURNS, which is what makes the
# phase affordable at all: research spending 4 of 12 turns would starve the
# cases planning exists to help.
PLAN_MAX_TURNS = 4

# A longer list is truncated, not refused - over-decomposing must not fail a run.
PLAN_MAX_STEPS = 6

# --- context caps ----------------------------------------------------------

# NFR-104 caps a tool result at 2,000 tokens, budgeted here in CHARACTERS at a
# conservative 3:1 so the check needs no tokeniser and no API key (NFR-602).
MAX_RESULT_CHARS = 6_000
TOOL_CAPS = {"read_file": 6_000, "write_file": 400, "run_shell": 6_000}
HEAD_LINES = 30
TAIL_LINES = 20

# --- tools (Phase L) -------------------------------------------------------

# Schemas are re-sent every request and cache_read_tokens is 0 on all 335
# recorded rows, so this is a per-turn tax. 10,000 is DERIVED: the largest cap
# at which NFR-402's median still holds. Re-measure if the provider changes.
MAX_SCHEMA_CHARS = 10_000

# Declared rather than discovered: a server offering a tool absent from `risk`
# gets it refused, never defaulted. Where fetch may go is bounded by the egress
# allowlist, not by this classification.
MCP_SERVERS = {
    "fetch": {
        "command": ["python", "-m", "mcp_server_fetch"],
        "risk": {"fetch": "read"},
    },
}

MCP_ENABLED = _env("AGENT_MCP", "on").strip().lower() not in ("0", "off", "false")

# A hung MCP server is the same failure as an unbounded shell command, which
# once held a scored suite for 25 minutes.
MCP_STARTUP_TIMEOUT = float(_env("AGENT_MCP_STARTUP_TIMEOUT", "30"))
MCP_CALL_TIMEOUT = float(_env("AGENT_MCP_CALL_TIMEOUT", "60"))

# Default ON, unlike planning: this closes a [M] requirement. The switch exists
# so the control run is the same binary with one tool removed.
WEB_ENABLED = _env("AGENT_WEB", "on").strip().lower() not in (
    "0", "off", "false")

# --- memory (Phase M) ------------------------------------------------------

# In the agent home, not the workspace: reset.sh wipes the workspace between
# runs, and a queue or a memory that vanished with it would be neither.
MEMORY_DB = AGENT_HOME / "memory.db"
TASKS_DB = AGENT_HOME / "tasks.db"
PROFILE = AGENT_HOME / "AGENT.md"           # durable user profile (FR-406)

MEMORY_ENABLED = _env("AGENT_MEMORY", "on").strip().lower() not in (
    "0", "off", "false")

# Injected into the system prompt, so charged per turn like the schemas.
# `memory_chars` is on every row so a recall win bought with tokens shows up.
MEMORY_INJECT_CHARS = 1_500
MEMORY_EPISODES = 3

# --- skills (Phase N) ------------------------------------------------------

# agentskills.io layout. Two roots, searched in precedence order: the project's
# (read-only) then the agent's own. AGENT_SKILLS_DIR replaces the first, and the
# harness always sets it - the benchmark's library describes a FICTIONAL project.
SKILLS_DIRS = (
    Path(os.environ["AGENT_SKILLS_DIR"]).resolve() if _env("AGENT_SKILLS_DIR")
    else Path(__file__).resolve().parent.parent / "skills",
    AGENT_HOME / "skills",
)

SKILLS_ENABLED = _env("AGENT_SKILLS", "on").strip().lower() not in (
    "0", "off", "false")

# The always-loaded index, charged every request. Overflow is FATAL rather than
# truncating: a silently shortened index drops whole skills from the agent's
# view, and one it cannot see is indistinguishable from one it did not choose.
SKILLS_INDEX_CHARS = 1_600

# Below MAX_RESULT_CHARS so a long document spills rather than arriving whole.
SKILL_BODY_CHARS = 6_000

# --- authoring (Phase O) ----------------------------------------------------

# Separate from SKILLS_ENABLED so loading-on/authoring-off is measurable.
# DEFAULT OFF: `learn` was called ZERO times in 15 valid sessions.
SKILL_AUTHORING = _env("AGENT_SKILL_AUTHORING", "off").strip().lower() not in (
    "0", "off", "false")

# 12 authored skills would already overflow SKILLS_INDEX_CHARS, so this bound
# and that one must be read together. Past the cap `learn` refuses and says so.
MAX_AUTHORED_SKILLS = 8

# --- deterministic extraction at `finish` (Phase O-redux) -------------------

# A rule instead of asking the model, which declined 15 times out of 15.
# Switchable independently of authoring or neither is attributable.
SKILL_EXTRACTION = _env("AGENT_SKILL_EXTRACTION", "off").strip().lower() not in (
    "0", "off", "false")

# The floor rejects a file carrying no procedure; the ceiling refuses a whole
# source file, which would eventually overflow the index and brick a run.
EXTRACT_MIN_CHARS = 80
EXTRACT_MAX_CHARS = 4_000
