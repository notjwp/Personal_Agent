"""shrink() — bound every tool result on the way into context (FR-401/402).

Returns a plain string, not a tuple: the spill path belongs inside the returned
text, where the model can act on it. That is the only place it was ever useful.

CE-05: the artifacts directory is created at call time, never at import.
"""
import os
from hashlib import sha256

from agent import config

# NFR-203: secrets never enter model context. Env-var indirection was already true
# - the key is read from the environment and never appears in a prompt - but the
# requirement's second half, output redaction, did not exist at all. A tool can
# echo a secret straight back: `run_shell(command="env")`, a traceback carrying a
# URL with credentials in it, a config file the agent was asked to read.
#
# Applied inside shrink() rather than at each tool, because shrink() is the ONE
# place every tool result passes through on its way into context (FR-401). Here it
# cannot be forgotten by a tool added later.
SECRET_SUFFIXES = ("_KEY", "_TOKEN", "_SECRET", "_PASSWORD", "_PASSWD",
                   "_CREDENTIALS")

# Below this length a value is likelier to be a coincidence than a secret: a
# two-character token would rewrite half of any English output.
MIN_SECRET_CHARS = 8


def redact(text: str) -> str:
    """Replace secret-shaped environment values with a named marker.

    Named rather than blanked. `[redacted:NVIDIA_API_KEY]` tells the model a
    credential was there and which one, so it can reason about what it read; a
    bare row of asterisks looks like data and gets copied into the next command.
    """
    for name, value in os.environ.items():
        if len(value) >= MIN_SECRET_CHARS and name.upper().endswith(SECRET_SUFFIXES):
            text = text.replace(value, f"[redacted:{name}]")
    return text


def shrink(tool: str, text: str) -> str:
    """Cap `text` for `tool`, spilling the full output to disk when it overflows."""
    # Before the cap AND before the spill: the artifact is readable with read_file,
    # so redacting only the returned string would leave the secret sitting on disk
    # inside the workspace, one tool call away.
    text = redact(text)
    cap = min(config.TOOL_CAPS.get(tool, config.MAX_RESULT_CHARS), config.MAX_RESULT_CHARS)
    if len(text) <= cap:
        return text

    config.ARTIFACTS.mkdir(parents=True, exist_ok=True)
    # Content-addressed: the same output twice reuses one artifact instead of
    # accumulating duplicates.
    artifact = config.ARTIFACTS / f"{sha256(text.encode('utf-8')).hexdigest()[:16]}.txt"
    artifact.write_text(text, encoding="utf-8")

    lines = text.splitlines()
    if len(lines) > config.HEAD_LINES + config.TAIL_LINES:
        head = "\n".join(lines[:config.HEAD_LINES])
        tail = "\n".join(lines[-config.TAIL_LINES:])
        hidden = len(lines) - config.HEAD_LINES - config.TAIL_LINES
        elided = f"[{hidden} lines elided, {len(text)} chars total]"
    else:
        # One enormous line: the line-based split cannot apply, so bound by chars.
        half = cap // 2
        head, tail = text[:half], text[-half:]
        elided = f"[{len(text) - 2 * half} chars elided, {len(text)} chars total]"

    # NFR-104 bounds CHARACTERS; the line branch above bounds LINES, and real test
    # output has long ones. Measured on the first real-repository run, not by a unit
    # test: 50 lines of pytest output became 11,340 chars against a 6,000 cap. The
    # practice fixtures never had lines long enough to expose it. Clamping here keeps
    # the bound true in both branches, and the spill path below still carries the
    # whole text - nothing is lost, only deferred to a read the model can choose.
    half = cap // 2
    head, tail = head[:half], tail[-half:]

    return (
        f"{head}\n... {elided} ...\n{tail}\n"
        f"[full output: {artifact}]\n"
        f'Inspect it with read_file(path="{artifact}", offset=N, limit=M), '
        f"or search it with run_shell(command='grep -n PATTERN {artifact}')."
    )
