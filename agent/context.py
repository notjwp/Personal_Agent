"""shrink() — bound every tool result on the way into context (FR-401/402).

Returns a plain string, not a tuple: the spill path belongs inside the returned
text, where the model can act on it. That is the only place it was ever useful.

CE-05: the artifacts directory is created at call time, never at import.
"""
from hashlib import sha256

from agent import config


def shrink(tool: str, text: str) -> str:
    """Cap `text` for `tool`, spilling the full output to disk when it overflows."""
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

    return (
        f"{head}\n... {elided} ...\n{tail}\n"
        f"[full output: {artifact}]\n"
        f'Inspect it with read_file(path="{artifact}", offset=N, limit=M), '
        f"or search it with run_shell(command='grep -n PATTERN {artifact}')."
    )
