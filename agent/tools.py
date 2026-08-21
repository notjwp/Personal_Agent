"""The three v1 tools and their hand-written schemas.

Schemas are hand-written on purpose (CE-02): a decorator plus its inspection
machinery costs ~25 lines plus ~5 per tool, against ~8 per tool written out.
Break-even is five tools; v1 has three. `agent/registry.py` arrives at tool six.

Tools RAISE on failure and never return an error string — the execute node owns
the exception-to-observation conversion (FR-208).

Tools do NOT re-check paths. The gate checks declared path arguments, and the
container's single writable mount bounds arbitrary shell. Two mechanisms guarding
one risk is what §13 cut the INSTALL set for.

Adding a tool touches this file only (NFR-601).
"""
import subprocess

from agent import config


def _int(value, default: int) -> int:
    """Coerce a numeric argument that arrived as a string.

    A declared JSON schema is a hint to the model, NOT enforcement: `"limit": 500`
    and `"limit": "500"` are both routinely emitted, and the second one crashed
    every read_file call in the first live session - so the agent rewrote a file
    it had never managed to read. Coerce at the boundary rather than trusting the
    schema, and treat a nonsense value as absent rather than as a crash.
    """
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def read_file(path: str, offset: int = 0, limit: int = 500) -> str:
    """Read `limit` lines starting at `offset` (0-based)."""
    offset, limit = _int(offset, 0), _int(limit, 500)
    target = config.WORKSPACE / path
    if target.is_dir():
        # Measured cost of NOT saying this: the only failure in the 14/15 baseline.
        # The agent asked for a directory, got a bare "[Errno 21] Is a directory",
        # retried the same path with a trailing slash, got the identical message,
        # and spent 3 of its 12 turns on it. That case passes in 11 turns.
        # An error the model cannot act on costs a turn every time it is retried.
        raise IsADirectoryError(
            f"{path} is a directory, not a file. "
            f"List it with run_shell(command='ls -la {path}').")
    lines = target.read_text(encoding="utf-8", errors="replace").splitlines()
    window = lines[offset:offset + limit]
    body = "\n".join(f"{offset + i + 1:6d}\t{line}" for i, line in enumerate(window))
    shown = f"{offset + 1}-{offset + len(window)}"
    return f"{path} (lines {shown} of {len(lines)})\n{body}"


def write_file(path: str, content: str) -> str:
    """Write `content` to `path`, replacing the file entirely."""
    target = config.WORKSPACE / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return f"wrote {path} ({len(content)} chars, {content.count(chr(10)) + 1} lines)"


def edit_file(path: str, old_string: str, new_string: str) -> str:
    """Replace `old_string` with `new_string`. The match must be unique.

    Why this exists, measured rather than assumed: `write_file` replaces a file
    ENTIRELY, so a five-line fix means emitting the whole file as a tool argument
    inside MAX_TOKENS - which caps thinking, text and arguments together. On the
    first real-repository baseline the files needing edits ran 559 to 2,689 lines;
    `rich/console.py` needs ~25,308 tokens to rewrite, 158% of one reply, so that
    case was impossible rather than merely expensive. Across 30 runs the agent made
    **11 writes against 352 reads** and scored 0/18, while every run that did manage
    a write fixed most of its failures.

    Exact matching only, deliberately. Hermes solves the same problem with a fuzzy
    patch format; that is hundreds of lines, and CE-02 says a framework earns its
    place at break-even at the CURRENT scale. If exact matching measurably fails
    because the model cannot reproduce strings precisely, the traces will show
    repeated edit errors and fuzzy matching is earned then, not now.
    """
    target = config.WORKSPACE / path
    if target.is_dir():
        raise IsADirectoryError(
            f"{path} is a directory, not a file. "
            f"List it with run_shell(command='ls -la {path}').")
    text = target.read_text(encoding="utf-8", errors="replace")

    found = text.count(old_string)
    if found == 0:
        # An error the model cannot act on costs a turn every time it is retried,
        # so name the tool that recovers from it.
        raise ValueError(
            f"that text was not found in {path}. Read the current contents with "
            f'read_file(path="{path}") and copy the text exactly, including '
            f"indentation and blank lines.")
    if found > 1:
        # Editing the first occurrence silently would corrupt the file in a way no
        # test necessarily catches. Refusing is the safety property of this tool.
        raise ValueError(
            f"that text appears {found} times in {path}; it must match exactly "
            f"once. Include more of the surrounding lines to make it unique.")

    target.write_text(text.replace(old_string, new_string), encoding="utf-8")
    delta = new_string.count(chr(10)) - old_string.count(chr(10))
    return (f"edited {path} (replaced {len(old_string)} chars with "
            f"{len(new_string)}, {delta:+d} lines)")


def run_shell(command: str, timeout: int = 120) -> str:
    """Run `command` in the workspace. Exit code, stdout and stderr are reported
    separately (FR-202)."""
    # Models emit schema-invalid arguments: this one arrived as the STRING "120"
    # on 2 of 5 calls in the first live run, crashing subprocess.run. Coerced here
    # rather than trusting the declared schema, because the OpenAI-compatible path
    # offers no strict-schema guarantee.
    done = subprocess.run(
        command, shell=True, cwd=config.WORKSPACE,
        capture_output=True, text=True, timeout=_int(timeout, 120),
    )
    return (
        f"exit code: {done.returncode}\n"
        f"--- stdout ---\n{done.stdout}\n"
        f"--- stderr ---\n{done.stderr}"
    )


TOOLS = {
    "read_file": {
        "fn": read_file,
        "schema": {
            "name": "read_file",
            "description": (
                "Read a text file from the workspace. Returns numbered lines. "
                "Use offset and limit to page through a large file."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "path": {"type": "string",
                             "description": "Path relative to the workspace root."},
                    "offset": {"type": "integer",
                               "description": "First line to return, 0-based. Default 0."},
                    "limit": {"type": "integer",
                              "description": "How many lines to return. Default 500."},
                },
                "required": ["path"],
            },
        },
    },
    "write_file": {
        "fn": write_file,
        "schema": {
            "name": "write_file",
            "description": (
                "Write a file in the workspace, replacing its entire contents. "
                "Read the file first; this does not patch, it overwrites."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "path": {"type": "string",
                             "description": "Path relative to the workspace root."},
                    "content": {"type": "string",
                                "description": "The complete new contents of the file."},
                },
                "required": ["path", "content"],
            },
        },
    },
    "edit_file": {
        "fn": edit_file,
        "schema": {
            "name": "edit_file",
            "description": (
                "Replace an exact snippet of a file with new text. Prefer this over "
                "write_file for any change to an existing file: it costs a few "
                "hundred characters instead of the whole file. The snippet must "
                "appear exactly once - include surrounding lines to make it unique."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "path": {"type": "string",
                             "description": "Path relative to the workspace root."},
                    "old_string": {"type": "string",
                                   "description": ("The exact text to replace, copied "
                                                   "from the file including indentation.")},
                    "new_string": {"type": "string",
                                   "description": "The text to put in its place."},
                },
                "required": ["path", "old_string", "new_string"],
            },
        },
    },
    "run_shell": {
        "fn": run_shell,
        "schema": {
            "name": "run_shell",
            "description": (
                "Run a shell command in the workspace. Returns the exit code, "
                "stdout and stderr separately. Use this to run tests."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "The command to run."},
                    "timeout": {"type": "integer",
                                "description": "Seconds before the command is killed. Default 120."},
                },
                "required": ["command"],
            },
        },
    },
}

# Order must stay deterministic: tools render first in the prompt, so reordering
# them invalidates the entire prompt cache on every request.
SCHEMAS = [entry["schema"] for entry in TOOLS.values()]
