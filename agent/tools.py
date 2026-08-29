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
import re
import subprocess
import sys
from pathlib import Path

from agent import config
from agent.registry import tool


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


SEARCH_HINT = "Search wider with run_shell(command='find . -type f | head -50')."


def _nearby(target) -> str:
    """What IS in the directory the agent guessed at, so the retry can be informed.

    Walks up to the nearest directory that exists, because a wrong guess deep in a
    tree ("src/models/user.py" when there is no "models") would otherwise report
    nothing at all. Bounded: a workspace of a thousand files must not produce a
    thousand-name error, which would only breach the result cap in a new way.
    """
    directory = target.parent
    while not directory.is_dir() and config.WORKSPACE in directory.parents:
        directory = directory.parent
    try:
        names = sorted(p.name + ("/" if p.is_dir() else "")
                       for p in directory.iterdir())
        where = directory.relative_to(config.WORKSPACE).as_posix() or "."
    except OSError:
        return SEARCH_HINT
    if not names:
        return f"{where} is empty. {SEARCH_HINT}"
    shown = ", ".join(names[:25])
    more = f" (+{len(names) - 25} more)" if len(names) > 25 else ""
    return f"{where} contains: {shown}{more}. {SEARCH_HINT}"


@tool(risk="read")
def read_file(path: str, offset: int = 0, limit: int = 500) -> str:
    """Read a text file from the workspace. Returns numbered lines. Use offset
    and limit to page through a large file.

    path: Path relative to the workspace root.
    offset: First line to return, 0-based. Default 0.
    limit: How many lines to return. Default 500.
    """
    offset, limit = _int(offset, 0), _int(limit, 500)
    target = config.WORKSPACE / path
    if target.is_dir():
        # FR-201 names "list directories" as a must-have and there was no tool for
        # it. There still is not, deliberately: a separate tool costs ~582 chars of
        # schema on EVERY request against a 6,000 cap, to answer a question this
        # tool is already being asked.
        #
        # It used to raise and tell the agent to run `ls`, which was already an
        # improvement on the bare "[Errno 21] Is a directory" that cost 3 of 12
        # turns on the only failure in the 14/15 baseline. But it still spends a
        # turn on a round trip, and the planning traces showed exactly that:
        # read_file on a directory, error, then `ls -la` on the same path. Two
        # turns for one answer. Returning the listing costs nothing and saves one.
        return f"{path} is a directory.\n{_nearby(target / '_')}"
    if not target.exists():
        # The same lesson as the directory error above, applied to the failure that
        # is FOUR TIMES more common. Across the trace archive, 82 of 112 read_file
        # errors are a missing file: the agent guesses a name, gets
        # "[Errno 2] No such file or directory", learns nothing about what to guess
        # next, and guesses again. Naming the siblings turns a retry loop into one
        # read - the same trade that took a case from 12 turns to 11.
        raise FileNotFoundError(f"{path} does not exist. {_nearby(target)}")
    lines = target.read_text(encoding="utf-8", errors="replace").splitlines()
    window = lines[offset:offset + limit]

    # Size the window so the result fits under the cap, rather than letting
    # shrink() take head+tail out of it afterwards.
    #
    # shrink() exists for UNEXPECTEDLY large output. A paged read is the opposite:
    # a deliberate, bounded request, and eliding its middle deletes exactly what
    # was asked for. Measured on real-rich: console.py is 101,228 chars, a
    # read_file(limit=500) renders 18,920, the cap is 6,000, and what arrived was
    # 30 head + 20 tail of the 500 lines requested - so the agent edited a file it
    # had only ever seen in fragments, and seeing all of it took 54 reads.
    #
    # NFR-104 is untouched: the result still fits the cap. Only its SHAPE changes,
    # from a window with a hole to a contiguous run of lines.
    cap = config.TOOL_CAPS.get("read_file", config.MAX_RESULT_CHARS)
    header_room = len(path) + 80          # the header line, plus room for the hint
    kept, used = [], header_room
    for i, line in enumerate(window):
        rendered = len(line) + 9          # 6-digit number, tab, newline
        if used + rendered > cap and kept:
            break
        kept.append(line)
        used += rendered

    body = "\n".join(f"{offset + i + 1:6d}\t{line}" for i, line in enumerate(kept))
    shown = f"{offset + 1}-{offset + len(kept)}"
    head = f"{path} (lines {shown} of {len(lines)})"
    if len(kept) < len(window):
        # Say how to continue. A silent truncation costs a turn the same way an
        # unactionable error does - the agent cannot ask for what it cannot see.
        head += (f" - narrowed to fit; continue with "
                 f"read_file(path=\"{path}\", offset={offset + len(kept)})")
    return f"{head}\n{body}"


@tool(risk="write")
def write_file(path: str, content: str) -> str:
    """Write a file in the workspace, replacing its entire contents. Read the
    file first; this does not patch, it overwrites.

    path: Path relative to the workspace root.
    content: The complete new contents of the file.
    """
    target = config.WORKSPACE / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return f"wrote {path} ({len(content)} chars, {content.count(chr(10)) + 1} lines)"


# Why edit_file exists, measured rather than assumed: `write_file` replaces a
# file ENTIRELY, so a five-line fix means emitting the whole file as a tool
# argument inside MAX_TOKENS - which caps thinking, text and arguments together.
# On the first real-repository baseline the files needing edits ran 559 to 2,689
# lines; `rich/console.py` needs ~25,308 tokens to rewrite, 158% of one reply, so
# that case was impossible rather than merely expensive. Across 30 runs the agent
# made 11 writes against 352 reads and scored 0/18, while every run that did
# manage a write fixed most of its failures.
#
# Exact matching only, deliberately. Hermes solves the same problem with a fuzzy
# patch format; that is hundreds of lines, and CE-02 says a framework earns its
# place at break-even at the CURRENT scale. If exact matching measurably fails
# because the model cannot reproduce strings precisely, the traces will show
# repeated edit errors and fuzzy matching is earned then, not now.
#
# The docstring below is the SCHEMA text now (FR-207), so notes that are for a
# human reader live here instead.
@tool(risk="write")
def edit_file(path: str, old_string: str, new_string: str) -> str:
    """Replace an exact snippet of a file with new text. Prefer this over
    write_file for any change to an existing file: it costs a few hundred
    characters instead of the whole file. The snippet must appear exactly once -
    include surrounding lines to make it unique.

    path: Path relative to the workspace root.
    old_string: The exact text to replace, copied from the file including indentation.
    new_string: The text to put in its place.
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


# `write` and NOT `destructive`: the DANGER regex in policy.py escalates the
# dangerous commands, so declaring the whole tool destructive would pause on
# every `ls`.
@tool(risk="write")
def run_shell(command: str, timeout: int = 120) -> str:
    """Run a shell command in the workspace. Returns the exit code, stdout and
    stderr separately. Use this to run tests.

    command: The command to run.
    timeout: Seconds before the command is killed. Default 120.
    """
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


# FR-203 wants three things back: stdout, the traceback if it raised, AND the
# value of the final expression. The last one is why this driver exists at all -
# `python -c` discards it, so `run_shell("python -c ...")` can never satisfy the
# requirement. Hermes does not do this either; its code tool is a subprocess
# runner like any other.
#
# The body is exec'd and a trailing EXPRESSION is eval'd separately, which is how
# a REPL distinguishes `x = 1` from `x`. A trailing statement is not an
# expression and correctly yields no value.
#
# ON THE exec/eval PAIR, since it looks alarming out of context: running arbitrary
# code IS this tool, and `ast.literal_eval` cannot satisfy FR-203 - it evaluates
# literals, not `sum(row.total for row in rows)`. The eval runs on an AST node
# parsed from the same source exec already ran, so it widens nothing. The boundary
# is elsewhere and unchanged: classify() gates the call at risk `write`, and it
# executes in the container, which `run_shell` already permits strictly more of -
# `run_shell(command="python -c ...")` was always available and is not gated any
# more tightly than this.
_PYTHON_DRIVER = """
import ast, sys, traceback

source = sys.stdin.read()
try:
    tree = ast.parse(source)
except SyntaxError:
    traceback.print_exc()
    raise SystemExit(1)

tail = tree.body.pop() if tree.body and isinstance(tree.body[-1], ast.Expr) else None
scope = {"__name__": "__main__"}
try:
    exec(compile(tree, "<agent>", "exec"), scope)
    if tail is not None:
        value = eval(compile(ast.Expression(tail.value), "<agent>", "eval"), scope)
        if value is not None:
            print("--- value ---")
            print(repr(value))
except BaseException:
    # Returned as text, never raised: FR-208 makes the execute node the one place
    # that turns an exception into an observation, and a traceback IS the answer
    # here rather than a failure of the tool.
    traceback.print_exc()
    raise SystemExit(1)
"""


@tool(risk="write")
def run_python(code: str, timeout: int = 120) -> str:
    """Run Python in the workspace. Returns stdout, the traceback if it raised,
    and the VALUE of the final expression - so end with a bare expression to see
    what it evaluates to, as in a REPL. Prefer this over run_shell for anything
    that computes: `python -c` throws the value away.

    code: Python source. The last line may be a bare expression to return its value.
    timeout: Seconds before it is killed. Default 120.
    """
    done = subprocess.run(
        [sys.executable, "-c", _PYTHON_DRIVER],
        input=code, cwd=config.WORKSPACE,
        capture_output=True, text=True, timeout=_int(timeout, 120),
    )
    return (
        f"exit code: {done.returncode}\n"
        f"--- stdout ---\n{done.stdout}\n"
        f"--- stderr ---\n{done.stderr}"
    )


# FR-206: repository inspection returning paths and line numbers, NOT file
# contents. That last clause is the requirement, and it is why `run_shell` with
# grep does not satisfy it: grep returns every matching line unbounded, which is
# the context flood shrink() exists to contain. Satisfying the letter of FR-206
# through run_shell would break the thing the letter protects.
#
# Bounds, all three deliberate:
#   MATCH_CAP     50 results, and the result SAYS when it truncated - a silent cut
#                 reads as "that is all there is" and the agent stops looking.
#   LINE_CHARS    120 chars of the matching line. Enough to judge relevance,
#                 not enough to be a way of reading a file.
#   rglob root    config.WORKSPACE, so FR-302 holds by construction rather than by
#                 trusting a pattern not to contain "../".
MATCH_CAP = 50
LINE_CHARS = 120

# Directories whose contents are never what anyone is searching for, and which
# dominate the result cap when included. Measured on this project: .git alone is
# thousands of files.
SKIP_DIRS = {".git", "__pycache__", ".pytest_cache", "node_modules", ".venv",
             ".agent", ".mypy_cache", ".tox", "dist", "build", ".eggs"}


# Implemented in Python rather than by shelling out to grep or ripgrep, so the
# walk is rooted at config.WORKSPACE and the gate does not have to parse a
# command line to know where the search was pointed. (`rg` is also not in the
# image.)
#
# THE ROOT ALONE IS NOT A BOUNDARY, which a test caught rather than review:
# `Path.glob("../*")` walks straight out of it, and the first version of this
# returned a file from the parent directory. FR-302 is enforced twice below -
# once on the pattern, so the refusal is something the agent can act on, and once
# on each resolved path, which is the check that actually holds because a symlink
# cannot be spotted in a pattern.
#
# Not reusing `policy._inside_workspace` on purpose: policy imports tools, and
# importing policy here would close the cycle.
@tool(risk="read")
def search_files(pattern: str, glob: str = "**/*", paths_only: bool = False) -> str:
    """Find where something appears in the workspace. Returns path:line: matches,
    never whole files. **Use this instead of run_shell with grep, find or ls** -
    it is bounded, so it cannot flood your context the way a raw grep across a
    large repository will. Use read_file once this has told you which file and
    which line to look at.

    pattern: Regular expression to search for.
    glob: Which files to search, e.g. '**/*.py'. Default all files.
    paths_only: Return only the file paths, one per file, without the matching lines. Default false.
    """
    if glob.startswith("/") or ".." in Path(glob).parts:
        raise ValueError(
            f"glob {glob!r} points outside the workspace. Patterns are relative "
            f"to the workspace root - use '**/*' to search everything, or "
            f"'**/*.py' for one file type.")
    try:
        matcher = re.compile(pattern)
    except re.error as exc:
        raise ValueError(
            f"{pattern!r} is not a valid regular expression: {exc}. "
            f"Escape regex characters to search for them literally.") from exc

    root = config.WORKSPACE.resolve()
    hits, total, scanned = [], 0, 0
    for target in sorted(config.WORKSPACE.glob(glob)):
        if not target.is_file() or SKIP_DIRS & set(target.parts):
            continue
        # .resolve() follows symlinks, so a link inside the workspace pointing
        # out resolves outside and is dropped here. Fails closed.
        resolved = target.resolve()
        if root not in resolved.parents:
            continue
        scanned += 1
        try:
            text = target.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue                      # unreadable is not a failure of the search
        where = resolved.relative_to(root).as_posix()
        for number, line in enumerate(text.splitlines(), 1):
            if not matcher.search(line):
                continue
            total += 1
            if len(hits) >= MATCH_CAP:
                continue
            hits.append(where if paths_only
                        else f"{where}:{number}: {line.strip()[:LINE_CHARS]}")

    if paths_only:
        # dict.fromkeys keeps first-seen order while collapsing repeats, so a file
        # with forty matches appears once.
        hits = list(dict.fromkeys(hits))

    if not hits:
        return (f"no match for {pattern!r} in {scanned} file(s) under {glob!r}. "
                f"Widen the glob, or check the pattern is a regular expression.")

    body = "\n".join(hits)
    if total > MATCH_CAP:
        body += (f"\n[{MATCH_CAP} of {total} matches shown. Narrow the pattern, "
                 f"or pass glob= to search fewer files.]")
    return body


# FR-207: the schema is DERIVED from the signature and docstring above, so this
# is the whole registration. What used to sit here was ~150 lines of dicts whose
# text had to be kept in step with six functions by hand.
#
# Order is deterministic and stays that way: tools render first in the prompt, so
# reordering them invalidates the prompt cache on every request. (Measured in
# Phase L: the current provider returns cache_read_tokens of 0, so that cache is
# not being hit at all today. The ordering costs nothing and pays off the day the
# provider changes.)
TOOLS = {fn.__name__: fn.spec for fn in (
    read_file, search_files, write_file, edit_file, run_python, run_shell)}

SCHEMAS = [entry["schema"] for entry in TOOLS.values()]


def toolset() -> dict:
    """Kept as the name every caller already uses; the merge itself moved.

    Three modules contribute tools now (built-ins, MCP, memory), so who owns the
    merged view became a real question and `agent/registry.py` is the answer -
    §12's "add at tool six" trigger, fired by `remember`.
    """
    from agent.registry import toolset as merged

    return merged()
