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
