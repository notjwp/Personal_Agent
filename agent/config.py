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

# NFR-304's third leg. Turns and tokens were capped; wall-clock was not, so work
# that spends neither - a shell command sitting inside its own timeout, a provider
# stalling through its retries - had nothing to stop it.
#
# WORKING seconds, accumulated by the two nodes that actually spend time, rather
# than wall-clock since the goal arrived. A thread resumed a week later must not
# terminate on its first turn because the calendar moved.
MAX_SECONDS = 900
# Compaction fires on CONTEXT SIZE, not on cumulative spend, and the distinction
# is not pedantic: spent_tokens only ever grows, so once compaction stopped being
# a terminal verdict the old `spent_tokens > 0.60 * budget` check would have
# fired on every subsequent turn forever - compact, act, compact, act. FR-403
# says "when context USE exceeds a configured fraction of budget", and context
# use is the thing compaction actually reduces.
#
# 45,000 chars is where the OLD trigger effectively fired, so behaviour stays
# comparable to every number already recorded. Measured over the 47 traces that
# reached it: context was 44,597 chars at the median, and compacting at that size
# removes 78% at the median and 60% at worst - both clear of NFR-403's 50%.
#
# Chars as a proxy for tokens at ~3:1, the same convention MAX_SCHEMA_CHARS and
# MAX_RESULT_CHARS use, so reflect needs no tokeniser and stays testable without
# an API key (NFR-602).
COMPACT_AT_CHARS = 45_000

# A compaction loop burns budget faster than the problem it solves. At the cap the
# run terminates as `stuck` rather than as a fifth verdict - FR-104 names four
# terminal outcomes and this is not a new kind of ending.
MAX_COMPACTIONS = 3

# --- planning (FR-101, FR-105, UR-02) --------------------------------------

# The kill switch, to the same standard as AGENT_MEMORY and AGENT_SKILLS: with
# this off the agent must be byte-identical to the one without planning - no
# phase, no injected instruction, no cursor, and reflect's original
# made-a-call termination guard.
#
# DEFAULT OFF, and measured that way rather than assumed. Across nine scored
# runs in three cycles the plan was NEVER written: `adopt` fell back to the goal
# copied verbatim every single time, because this provider keeps emitting tool
# calls once the history contains them - proven directly, with `tools` absent
# from the request entirely and finish_reason still `tool_calls`. So FR-101 is
# not satisfied by what is built, the phase costs ~10% more tokens, and it
# pushed the median past NFR-402's 60,000. On by default would be a capability
# claim the runs do not support. See eval/CHANGELOG.md.
PLAN_ENABLED = os.environ.get("AGENT_PLAN", "off").strip().lower() not in (
    "0", "off", "false")

# Planning turns are capped SEPARATELY and are not charged against MAX_TURNS.
#
# This is the whole reason the phase is affordable. MAX_TURNS is 12: research
# that spent four of them would starve exactly the cases planning exists to help
# - add-endpoint, which supplied 18 of the dev split's 32 stuck-at-cap runs, has
# a 12-turn budget and currently passes 1 of 3. A shared counter would make this
# layer worse, and predictably so.
PLAN_MAX_TURNS = 4

# Section 3: "decompose goal into 2-6 steps". A longer list is truncated rather
# than refused - a planner that over-decomposes must not fail the run.
PLAN_MAX_STEPS = 6

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
# on every run, paid whether a tool is used or not. `cache_read_tokens` is 0 on
# all 335 scored rows this project has recorded, so nothing here is ever cached:
# a schema costs its full price once per model call, every call.
#
# RAISED 6,000 -> 10,000 on 2026-08-23, and this time the number is DERIVED rather
# than judged. The 6,000 was sized against NFR-402's 60,000-token CEILING, on the
# assumption a run would approach it. Runs turned out to cost about half that, so
# the same cap quietly permitted schemas to dominate - at 6,000 the median run
# would have been 38,640 tokens, of which 24,000 was tool definitions.
#
# Every recorded run replayed with the schema resized (335 rows, median 12 model
# calls, the project's measured ~3 chars/token):
#
#      cap   schema/run   median run   vs NFR-402   runs breaching   ~tools
#    4,075       16,296       26,621          44%              2%         7  <- today
#    6,000       24,000       38,640          64%             10%        10
#    8,000       31,992       46,632          78%             27%        13
#   10,000       39,996       54,636          91%             38%        17  <- here
#   12,000       48,000       62,640         104%             53%        20
#
# 10,000 is the LARGEST cap at which NFR-402 still holds: the median lands at
# 54,636 with 9% of margin, and at 12,000 the median itself breaches. That is the
# whole justification - it is a ceiling, not a preference.
#
# TWO THINGS THIS CAP NO LONGER PROMISES, stated because the old one did:
#
#   - At FULL usage, 38% of individual runs would exceed 60,000 tokens. NFR-402
#     bounds the MEDIAN, so that is legal by its own wording, but the cap can no
#     longer be read as "nothing will breach the cost ceiling".
#   - Exposing 17 tools is affordable in the sense that the suite would still
#     pass. It would also spend 40,000 tokens per run on definitions before any
#     work happens, which is a trade to make deliberately rather than to
#     discover.
#
# The cap is a REFUSAL threshold, not a spend: raising it costs nothing until a
# tool is actually added. What it changed is how much it still protects.
#
# If the provider changes, RE-MEASURE before touching this again. The entire
# derivation rests on cache_read_tokens being 0; on a caching endpoint the schema
# is paid once per RUN instead of once per CALL and this line item drops ~90%.
MAX_SCHEMA_CHARS = 10_000

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

# --- authoring (Phase O) ----------------------------------------------------

# Separate from SKILLS_ENABLED on purpose: Phase O's control has LOADING on and
# authoring off, so the two must be switchable independently or the comparison
# measures the wrong thing.
# DEFAULT OFF. Phase O measured `learn` called ZERO times in 15 valid sessions - tool
# exposed, an explicit instruction in SOUL.md, three turn budgets, and a task small
# enough to finish with turns to spare. The model does not treat "record this for
# later" as part of the job. The code stays because the NEXT attempt reuses it, but a
# capability with no measured effect does not ship on by default.
SKILL_AUTHORING = os.environ.get("AGENT_SKILL_AUTHORING", "off").strip().lower() not in (
    "0", "off", "false")

# How many skills the agent may write for itself.
#
# Not arbitrary: the index is charged on EVERY request and overflowing
# SKILLS_INDEX_CHARS is fatal by design, so an agent writing one skill per session
# would eventually brick its own runs. At ~150 chars per index line, 12 authored
# skills is ~1,800 chars - already past the 1,600 cap on its own, so this bound
# and that one have to be read together. Past the cap `learn` refuses and says so.
MAX_AUTHORED_SKILLS = 8

# --- deterministic extraction at `finish` (Phase O-redux) -------------------

# Separate from SKILL_AUTHORING because they are different mechanisms answering
# the same need: authoring asks the MODEL to decide what is worth keeping, and
# Phase O measured it declining 15 times out of 15. Extraction decides with a rule
# instead, so the two must be switchable independently or neither is attributable.
SKILL_EXTRACTION = os.environ.get("AGENT_SKILL_EXTRACTION", "off").strip().lower() not in (
    "0", "off", "false")

# Size bounds on an extracted document, in characters.
#
# The floor rejects a one-line file carrying no procedure. The ceiling refuses a
# whole source file: the skill index is charged on EVERY request and overflowing
# SKILLS_INDEX_CHARS is fatal by design, so an unbounded extract would eventually
# brick its own runs.
EXTRACT_MIN_CHARS = 80
EXTRACT_MAX_CHARS = 4_000
