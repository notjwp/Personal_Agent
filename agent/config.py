"""Single source of truth for every tunable in the system.

FR-302 (reject paths outside the workspace) and NFR-201 (zero writes outside the
workspace) both depend on WORKSPACE having exactly one definition. It is never
re-derived in another module, in any language: scripts/reset.sh reads the same
environment variable rather than carrying a second default.
"""
import os
from pathlib import Path

# --- locations -------------------------------------------------------------

# The container sets AGENT_WORKSPACE. reset.sh fails loudly when it is unset,
# so the default string below exists in exactly one place.
WORKSPACE = Path(os.environ.get("AGENT_WORKSPACE", "/workspace")).resolve()

# Artifacts live INSIDE the workspace so the model can read a spilled file
# (§4.3) without tripping FR-302. Checkpoints live OUTSIDE it so they survive
# reset.sh, which wipes the workspace between cases.
ARTIFACTS = WORKSPACE / ".agent" / "artifacts"

# The second declared writable root (Phase K). It was /app/.agent, inside the
# project tree - which only worked because the project was mounted writable, and
# that turned out to make the harness and the fixtures writable too. /app is now
# read-only, so state moves out rather than the mount widening back.
AGENT_HOME = Path(os.environ.get("AGENT_HOME", "/state")).resolve()
STATE_DB = AGENT_HOME / "state.db"

# --- model -----------------------------------------------------------------

# Which adapter implementation runs. "anthropic" is the eventual target; "nvidia"
# is an OpenAI-compatible endpoint with a free key, used while there are no
# Anthropic credits. Flipping back is one environment variable.
PROVIDER = os.environ.get("AGENT_PROVIDER", "nvidia")

# Anthropic path
MODEL = "claude-opus-5"
EFFORT = "medium"        # low | medium | high | xhigh | max — swept in Phase D

# OpenAI-compatible path (NVIDIA NIM by default). The model default is a starting
# guess only: tool-calling reliability varies enormously between open-weight models,
# and the spec forbids parsing calls out of free text, so there is no fallback if it
# turns out to be poor. Probe before trusting it.
# Any OpenAI-compatible endpoint: NVIDIA NIM, OpenAI, OpenRouter, Groq, Together,
# a local Ollama. The names are OPENAI_* because that is the protocol being spoken,
# not the vendor; NIM_* is kept as a fallback so existing .env files keep working.
OPENAI_BASE_URL = (os.environ.get("OPENAI_BASE_URL")
                   or os.environ.get("NIM_BASE_URL")
                   or "https://integrate.api.nvidia.com/v1")
# Chosen by probe, not reputation. The history matters:
#   llama-3.3-70b  passed the probe, then its endpoint stopped responding
#                  entirely (POST times out while GET /models returns 200 in
#                  0.6s - an NVIDIA-side capacity fault). Still down later.
#   llama-3.1-70b  produced the 4/15 baseline. It reads well and acts badly:
#                  9 of 15 runs never called read_file, and one case invented
#                  two files that do not exist in the project.
#   nemotron-3-super-120b  sustains multi-turn tool calling at 0.8-3.8s per
#                  turn (5-10x faster) and opens with run_shell pytest then
#                  read_file on the failing test - the read-before-edit
#                  behaviour the previous model skipped.
#
# Changing this invalidates any existing baseline: it is a different
# measurement, not a tuning delta. Every trace row records provider and model.
OPENAI_MODEL = (os.environ.get("OPENAI_MODEL")
                or os.environ.get("NIM_MODEL")
                or "nvidia/nemotron-3-super-120b-a12b")

MAX_TOKENS = 16_000      # on Anthropic this caps thinking AND response text together

# Wall-clock caps. Turn and token caps alone do not bound a run: a single request
# that never returns hangs forever, and the SDK defaults (10 min x 3 attempts)
# mean half an hour of silence before anything surfaces.
REQUEST_TIMEOUT = float(os.environ.get("AGENT_REQUEST_TIMEOUT", "120"))
MAX_ATTEMPTS = 3         # initial call + 2 retries, matching the spec's cap


def _clean(value: str | None) -> str:
    """Strip whitespace and surrounding quotes.

    Docker's --env-file does NOT strip quotes, while almost every other .env tool
    does. A line written as KEY="abc" therefore arrives as the six characters
    "abc" including the quotes, and the only symptom is an authentication failure
    that looks nothing like a quoting problem.
    """
    value = (value or "").strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        value = value[1:-1]
    return value.strip()


# Checked in order. AGENT_API_KEY is the provider-neutral one to prefer: the rest
# are vendor-named and kept because existing .env files use them. Putting an
# OpenRouter key in NVIDIA_API_KEY works but reads as a mistake, which is the wart
# this ordering exists to retire.
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
COMPACT_AT = 0.60        # reflect check (a): compact above this fraction of budget

# --- context caps ----------------------------------------------------------

# NFR-104 caps a tool result at 2,000 tokens. Budgeted here in CHARACTERS at a
# conservative 3 chars/token, because a token-accurate check needs an API call
# and NFR-602 requires the unit tests to run without a key. The harness performs
# the exact token check separately, where a key is present.
MAX_RESULT_CHARS = 6_000
TOOL_CAPS = {"read_file": 6_000, "write_file": 400, "run_shell": 6_000}
HEAD_LINES = 30
TAIL_LINES = 20

# --- tools (Phase L) -------------------------------------------------------

# The tool schemas are re-sent on EVERY request, so their size is a per-turn tax
# on every run, paid whether a tool is used or not. Measured on this project
# before any of MCP was built:
#
#   4 built-in tools     1,997 chars  ~665 tokens
#   x 9.1 model calls    ~6,050 tokens per run, against a median run of 26,600
#                        - so 23% of a run was already tool schema
#   cache_read_tokens    0 on all 15 rows of the Phase K regression run
#
# That last line is why this cap exists. On a provider that caches prompts the
# schema is paid once; on this one it is paid every turn, which makes tool breadth
# the most expensive thing the project can buy. At ~254 chars-per-token-ish rates
# measured against a real MCP server, 24 exposed tools would add ~61,500 tokens per
# run and breach NFR-402's 60,000 median ceiling on schema ALONE.
#
# 6,000 chars is ~2,000 tokens/call and ~18,000/run, which leaves the median well
# inside NFR-402. It is roughly eight tools.
#
# If the provider changes, RE-MEASURE before raising this: the number is a
# consequence of one endpoint's caching behaviour on one day, not a law.
MAX_SCHEMA_CHARS = 6_000

# MCP servers started for a run, and the risk of every tool they expose.
#
# Declared here rather than discovered, because §11's non-goal on dynamic tool
# loading is amended only for servers that are baked into the image at build time
# and whose tools are risk-classified BEFORE they are offered to the model. A
# server that offers a tool absent from `risk` gets that tool refused, never
# defaulted - see policy.register().
#
# `fetch` is "read": it retrieves a URL and cannot modify anything. Where it may
# go is bounded by the egress allowlist, which is per case-run (Phase K), not by
# this classification.
MCP_SERVERS = {
    "fetch": {
        "command": ["python", "-m", "mcp_server_fetch"],
        "risk": {"fetch": "read"},
    },
}

# The kill switch. A capability that cannot be turned off cannot be attributed
# either: every scored row records whether MCP was on, and with it off the agent
# must fall back to exactly the four built-in tools.
MCP_ENABLED = os.environ.get("AGENT_MCP", "on").strip().lower() not in ("0", "off", "false")

# Bounds on a third party's process. `run_shell` already carries this scar: an
# unbounded check command held a scored suite for 25 minutes. A hung MCP server is
# the same failure wearing a different name, so it gets the same treatment.
MCP_STARTUP_TIMEOUT = float(os.environ.get("AGENT_MCP_STARTUP_TIMEOUT", "30"))
MCP_CALL_TIMEOUT = float(os.environ.get("AGENT_MCP_CALL_TIMEOUT", "60"))

# --- memory (Phase M) ------------------------------------------------------

# Both live in the agent home, which is the whole reason Phase K created it:
# reset.sh wipes the workspace between runs by design, so anything learned has to
# sit outside it.
MEMORY_DB = AGENT_HOME / "memory.db"          # episodes, FTS5 (FR-407)
PROFILE = AGENT_HOME / "AGENT.md"             # durable user profile (FR-406)

# The kill switch, and the reason the comparison in eval/CHANGELOG.md is a
# controlled one rather than two separate numbers: with this off the agent must be
# byte-identical to the one without memory - same prompt, same tools, no injection.
MEMORY_ENABLED = os.environ.get("AGENT_MEMORY", "on").strip().lower() not in (
    "0", "off", "false")

# What retrieval may inject, in CHARACTERS.
#
# Budgeted for the same reason MAX_SCHEMA_CHARS is: the injected text goes into the
# system prompt, which is re-sent on every request, and the measured provider
# returned cache_read_tokens of 0 on every row of a scored run. So this is charged
# per turn - 1,500 chars is ~500 tokens, ~4,500 per run at 9 model calls.
#
# It is a budget, not a guess, and `memory_chars` is recorded on every row so a
# recall win bought with a large token increase shows up as the trade it is.
MEMORY_INJECT_CHARS = 1_500
MEMORY_EPISODES = 3           # how many past sessions retrieval may return

# --- skills (Phase N) ------------------------------------------------------

# On-demand knowledge documents, in the agentskills.io layout: one directory per
# skill holding a SKILL.md with YAML frontmatter (`name`, `description`) and any
# reference files or scripts it bundles.
#
# TWO roots, searched in order, and the order is the precedence:
#   PROJECT   ships with the repository, read-only in the container
#   AGENT_HOME  the agent's own, writable - empty until Phase O lets it author
#
# AGENT_SKILLS_DIR replaces the project root when set, and the eval harness always
# sets it. The benchmark's library describes a FICTIONAL project - `-quartz` version
# suffixes, `check_` test names - and living at the repository root it read as this
# project's own conventions, to a human browsing the tree and to the agent working
# in it. Fixtures belong under eval/fixtures with every other fixture.
SKILLS_DIRS = (
    Path(os.environ["AGENT_SKILLS_DIR"]).resolve() if os.environ.get("AGENT_SKILLS_DIR")
    else Path(__file__).resolve().parent.parent / "skills",
    AGENT_HOME / "skills",
)

# The kill switch. With this off there is no index, no `load_skill` tool, and the
# agent is identical to the Phase M one.
SKILLS_ENABLED = os.environ.get("AGENT_SKILLS", "on").strip().lower() not in (
    "0", "off", "false")

# What the ALWAYS-LOADED index may cost, in characters.
#
# This is the whole progressive-disclosure argument, and it is a budget for the
# same reason MAX_SCHEMA_CHARS is: the index sits in the system prompt, which is
# re-sent on every request, and the measured provider returned cache_read_tokens
# of 0 on every row of a scored run.
#
# Level 1 (name + description) is ~40 tokens per skill and is always paid.
# Level 2 (the body) and Level 3 (bundled files) are ~600+ tokens and are paid
# ONLY when the agent decides a skill applies.
#
# 1,600 chars is ~530 tokens, ~4,800 per run at 9 model calls, and holds the eight
# shipped skills with headroom. The first guess was 1,200 and the eight-skill index
# measured 1,236 - caught before any quota was spent, and the reason the overrun is
# now FATAL rather than truncating: a silently shortened index drops whole skills
# from the agent's view, and a skill it cannot see is indistinguishable from one it
# chose not to use. Past this, trim the descriptions rather than raise the cap.
SKILLS_INDEX_CHARS = 1_600

# One skill body is a tool result like any other and is bounded as one. Kept below
# MAX_RESULT_CHARS so a long document spills to an artifact rather than arriving
# whole - shrink() already does that, this just makes the intent explicit.
SKILL_BODY_CHARS = 6_000
