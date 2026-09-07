"""First-run credentials, resolved before anything else reads config.

Not in `cli.py`, and the reason is mechanical rather than aesthetic:
`config.py` resolves every tunable at IMPORT time, so a wizard that runs after
`agent.config` has been imported cannot change PROVIDER, OPENAI_MODEL or
OPENAI_BASE_URL no matter what it writes. It runs from `agent/__main__.py`, in
the same window `.env` is loaded in, and the entry point reloads config after.

Nothing here imports `textual`. `run()` pulls the screen in inside the function
body, so a machine that reaches `needed() == False` never pays for it
(NFR-602), and everything a test needs to check - the probe's classification,
what gets written, what the key looks like when shown - is reachable without a
running app.
"""
import os
import sys
import traceback
from dataclasses import dataclass
from pathlib import Path

ENV_FILE = Path(__file__).resolve().parent.parent / ".env"

# 120s x 6 attempts is sized for a working turn. A person waiting at a prompt
# is a different budget, and one they judge by their own patience.
PROBE_TIMEOUT = 20.0


@dataclass(frozen=True)
class Choice:
    provider: str
    model: str
    note: str


# Exactly what `provider.call_model` dispatches on, and nothing aspirational.
CHOICES = (
    Choice("nvidia", "nvidia/nemotron-3-super-120b-a12b",
           "default - every baseline in this repo"),
    Choice("nvidia", "nvidia/nemotron-3-ultra-550b-a55b",
           "4.6x larger; measured 10/18, the same as super"),
    Choice("anthropic", "claude-opus-5", ""),
    Choice("anthropic", "claude-sonnet-5", ""),
    Choice("anthropic", "claude-haiku-4-5-20251001", ""),
    Choice("openai", "", "custom - OpenRouter, Groq, OpenAI, a local Ollama"),
)

WARNING = ("changing the model invalidates every baseline in this repo - "
           "it is a different measurement.")

# The app cannot make its own window transparent; only the emulator can.
OPACITY_HINT = ('Windows Terminal: add "opacity": 85 and "useAcrylic": true to '
                'the profile. kitty background_opacity, WezTerm '
                'window_background_opacity, Alacritty opacity.')


# ----------------------------------------------------------- when it runs

def provider_name() -> str:
    """The provider as the environment has it, read late and never cached."""
    return (os.environ.get("AGENT_PROVIDER") or "nvidia").strip().lower()


def needed() -> bool:
    """Whether the RESOLVED provider has no key.

    Provider-specific because the keys are: an Anthropic key does not
    authenticate NVIDIA, so a wizard that only asked "is any key set" would
    skip itself on a machine that cannot make a single call.
    """
    from agent import config

    if provider_name() == "anthropic":
        return not (os.environ.get("ANTHROPIC_API_KEY") or "").strip()
    try:
        config.openai_api_key()
    except RuntimeError:
        return True
    return False


def _tty(stream) -> bool:
    """Whether a stream is a terminal, for streams that may not be one at all.

    pythonw, a service host and a closed pipe each hand over something without
    a usable isatty, and this runs on EVERY `python -m agent`.
    """
    try:
        return bool(stream.isatty())
    except (AttributeError, ValueError):
        return False


def interactive() -> bool:
    """A terminal with a person at it.

    This is what keeps --worker, --channel, the scheduled tasks and the eval
    harness from ever blocking on a prompt; without a TTY they fall through to
    config.openai_api_key()'s existing, actionable RuntimeError.
    """
    return bool(_tty(sys.stdin) and _tty(sys.stdout)
                and not os.environ.get("AGENT_NO_SETUP"))


def run() -> bool:
    """Open the wizard; True if anything was saved."""
    from agent.ui.setup import SetupApp

    return bool(SetupApp(existing_key=current_key()).run())


def current_key() -> str:
    """Whatever key the resolved provider would use right now, or ""."""
    from agent import config

    if provider_name() == "anthropic":
        return (os.environ.get("ANTHROPIC_API_KEY") or "").strip()
    try:
        return config.openai_api_key()
    except RuntimeError:
        return ""


# ----------------------------------------------------------------- probe

def mask(key: str) -> str:
    """Enough to recognise which key it is, never enough to use it."""
    key = (key or "").strip()
    if len(key) < 12:
        return "•" * 8
    return f"{key[:6]}{'•' * 8}{key[-4:]}"


def scrub(text: str, key: str) -> str:
    """A provider is free to quote the key back in its own error message, and
    that message is about to be rendered (NFR-203). Applied here AND at the
    render boundary, because a message can arrive from somewhere else."""
    return text.replace(key, mask(key)) if key else text


def probe(chosen: str, model: str, base_url: str, key: str) -> tuple[str, str]:
    """Ask the endpoint one question with the settings ABOUT to be saved.

    Costs roughly 40 tokens. The config module is set for the duration rather
    than written first and read back, because a wizard that verifies the
    configuration already on disk verifies nothing - and because a key that
    fails must never have been persisted.
    """
    from agent import config
    from agent import provider as api

    settings = ("PROVIDER", "MODEL", "OPENAI_MODEL", "OPENAI_BASE_URL",
                "REQUEST_TIMEOUT", "CALL_ATTEMPTS")
    before = {name: getattr(config, name) for name in settings}
    keys = {name: os.environ.get(name)
            for name in ("AGENT_API_KEY", "ANTHROPIC_API_KEY")}
    try:
        config.PROVIDER = chosen
        config.REQUEST_TIMEOUT = PROBE_TIMEOUT
        config.CALL_ATTEMPTS = 1
        if chosen == "anthropic":
            config.MODEL = model
            os.environ["ANTHROPIC_API_KEY"] = key
        else:
            config.OPENAI_MODEL = model
            # Blank means "the configured endpoint", which is right for the
            # named NVIDIA choices. The screen refuses a blank one for `custom`,
            # where it would verify one endpoint and then configure another.
            config.OPENAI_BASE_URL = base_url or config.OPENAI_BASE_URL
            os.environ["AGENT_API_KEY"] = key
        api.call_model([{"role": "user",
                         "content": [{"type": "text", "text": "say ok"}]}],
                       "Answer with one word.", [])
    except api.ProviderMisconfigured as exc:
        return "misconfigured", scrub(str(exc), key)
    except api.ProviderUnavailable as exc:
        return "unavailable", scrub(str(exc), key)
    except Exception:                                     # noqa: BLE001
        # Ours, and it stays loud. Excusing a real bug as a flaky endpoint is
        # how a wizard comes to save a key against a provider it never reached.
        return "error", scrub(traceback.format_exc(limit=3).strip()[-400:], key)
    finally:
        for name, value in before.items():
            setattr(config, name, value)
        for name, value in keys.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
    return "ok", ""


# ----------------------------------------------------------- persistence

def variables(chosen: str, model: str, base_url: str, key: str) -> dict[str, str]:
    """The names each provider is configured through."""
    if chosen == "anthropic":
        return {"AGENT_PROVIDER": "anthropic", "AGENT_MODEL": model,
                "ANTHROPIC_API_KEY": key}
    pairs = {"AGENT_PROVIDER": chosen, "OPENAI_MODEL": model,
             "AGENT_API_KEY": key}
    if chosen == "openai":
        pairs["OPENAI_BASE_URL"] = base_url
    return pairs


def write_env(pairs: dict[str, str], path: Path | None = None) -> None:
    """Persist to `.env` AND to this process, so nothing needs a restart.

    Replaces an existing line rather than appending a second: `_load_env` takes
    the FIRST occurrence and a real environment variable beats both, so a
    duplicate leaves a file whose behaviour does not match its last line.
    """
    path = path or ENV_FILE
    lines = path.read_text(encoding="utf-8").splitlines() if path.is_file() else []
    left = dict(pairs)
    out = []
    for line in lines:
        name = line.split("=", 1)[0].strip() if "=" in line else ""
        out.append(f"{name}={left.pop(name)}" if name in left else line)
    out += [f"{name}={value}" for name, value in left.items()]
    # newline="" with an explicit \n, and values never quoted: this file is read
    # by Docker --env-file, which strips neither CRLF nor quotes.
    with open(path, "w", encoding="utf-8", newline="") as handle:
        handle.write("\n".join(out) + "\n")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass                     # Windows has no mode bits; the write still stands
    os.environ.update(pairs)
