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
STATE_DB = Path(os.environ.get("AGENT_HOME", "/app/.agent")).resolve() / "state.db"

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
OPENAI_BASE_URL = os.environ.get("NIM_BASE_URL", "https://integrate.api.nvidia.com/v1")
# llama-3.3-70b was the first choice and passed the tool-calling probe, but its
# hosted endpoint later stopped responding entirely (POST /chat/completions times
# out while GET /models returns 200 in 0.6s — an NVIDIA-side capacity problem, not
# an auth or quota one). 3.1-70b answers in ~1.3s with well-formed tool calls.
OPENAI_MODEL = os.environ.get("NIM_MODEL", "meta/llama-3.1-70b-instruct")

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


def openai_api_key() -> str:
    """Read at call time, never at import: importing config must not require a key."""
    key = _clean(os.environ.get("NVIDIA_API_KEY")) or _clean(os.environ.get("OPENAI_API_KEY"))
    if not key:
        raise RuntimeError(
            "no API key for the OpenAI-compatible provider. Set NVIDIA_API_KEY "
            "(free, no card, from build.nvidia.com), or set AGENT_PROVIDER=anthropic "
            "and ANTHROPIC_API_KEY instead.")
    return key

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
