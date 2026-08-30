"""The built-in tools, and the risk each one carries.

Seven of them, and their schemas are DERIVED from the signature and docstring by
`@tool` in agent/registry.py (FR-207). They were hand-written until tool eight,
when §13's arithmetic - ~25 lines plus ~5 per tool for the machinery against ~8
per tool written out - stopped favouring the dicts.

Tools RAISE on failure and never return an error string — the execute node owns
the exception-to-observation conversion (FR-208).

Tools do NOT re-check paths. The gate checks declared path arguments, and the
container's single writable mount bounds arbitrary shell. Two mechanisms guarding
one risk is what §13 cut the INSTALL set for.

Adding a tool touches this file only (NFR-601).
"""
import difflib
import os
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
        # FR-201's "list directories": read_file on a directory returns the listing
        # rather than an error, so the agent needs no second tool to look around.
        return f"{path} is a directory.\n{_nearby(target / '_')}"
    if not target.exists():
        # A wrong path is a guess, so the error names what IS in the nearest real
        # directory - a bare "not found" gets the same wrong guess again.
        raise FileNotFoundError(f"{path} does not exist. {_nearby(target)}")
    lines = target.read_text(encoding="utf-8", errors="replace").splitlines()
    window = lines[offset:offset + limit]

    # The window is sized so the result fits under the cap. Returning a slice the
    # caller must then re-slice was measured worse than returning fewer lines whole.
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


# edit_file exists because whole-file writes cost the run: on real repositories
# the agent could not afford to rewrite a file it had only partly read. Its
# description is load-bearing - this wording took real repos 0/9 to 4/7.
# The agent has no reason to trust an edit it cannot see. A character count is
# not evidence, so it re-reads to check - and re-reading is what the thrash
# detector then punishes. A diff is the receipt; the counts stay for scale.
DIFF_LINES = 40
# DIFF_LINES bounds LINES; this bounds CHARACTERS. A test caught the gap -
# 40 lines of 300 chars is 12,000, double the result cap. Same trap shrink()
# already carries.
DIFF_LINE_CHARS = 120


def _edit_receipt(path: str, before: str, after: str) -> str:
    """A unified diff of the change, bounded so it cannot flood context.

    Past DIFF_LINES the diff stops being a receipt and becomes a second copy
    of the file, so it degrades to the counts plus the first hunk.
    """
    delta = after.count(chr(10)) - before.count(chr(10))
    counts = f"edited {path} ({delta:+d} lines)"
    diff = [l[:DIFF_LINE_CHARS] for l in difflib.unified_diff(
        before.splitlines(), after.splitlines(),
        fromfile=f"{path} before", tofile=f"{path} after", lineterm="", n=2)]
    if not diff:
        return counts + " - no textual change"
    if len(diff) > DIFF_LINES:
        shown = diff[:DIFF_LINES]
        shown.append(f"... [{len(diff) - DIFF_LINES} more diff lines; read the file if you need them]")
        diff = shown
    return counts + ":" + chr(10) + chr(10).join(diff)


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

    updated = text.replace(old_string, new_string)
    target.write_text(updated, encoding="utf-8")

    # A write that did not land must not report success. Hermes makes this a
    # hard error rather than a silent flag and that is the right call.
    if target.read_text(encoding="utf-8", errors="replace") != updated:
        raise RuntimeError(
            f"the edit to {path} did not persist - the file on disk differs "
            f"from what was written. Re-read it and retry.")

    return _edit_receipt(path, text, updated)


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
    # Models emit schema-invalid arguments - this one arrived as the string "120s".
    # Coerce at the boundary; a declared type is a hint, not enforcement.
    done = subprocess.run(
        command, shell=True, cwd=config.WORKSPACE,
        capture_output=True, text=True, timeout=_int(timeout, 120),
    )
    return (
        f"exit code: {done.returncode}\n"
        f"--- stdout ---\n{done.stdout}\n"
        f"--- stderr ---\n{done.stderr}"
    )


# FR-203 wants stdout, the traceback if it raised, AND the final expression's
# value - a REPL's contract, not a script's. Implemented in-process so the value
# survives; a subprocess would lose it.
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


# FR-206: paths and line numbers, never file contents. That clause is the
# requirement - grep returns every matching line unbounded, which is the context
# flood shrink() exists to contain.
MATCH_CAP = 50
LINE_CHARS = 120

# Directories whose contents are never what anyone is searching for, and which
# dominate the result cap when included. Measured on this project: .git alone is
# thousands of files.
SKIP_DIRS = {".git", "__pycache__", ".pytest_cache", "node_modules", ".venv",
             ".agent", ".mypy_cache", ".tox", "dist", "build", ".eggs"}


# Python rather than grep/ripgrep so the walk is rooted at WORKSPACE and the
# gate need not parse a command line. THE ROOT ALONE IS NOT A BOUNDARY: a test
# caught Path.glob("../*") escaping it, so FR-302 is enforced on resolved paths.
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


# --------------------------------------------------------------------- FR-501
#
# Ranked title + URL + snippet, never page bodies. The engine fan-out is
# load-bearing: pinning one backend rate-limits hard, so there is no retry.
# Measured numbers and WEB_HOSTS' rationale are in eval/CHANGELOG.md Stage 4.
WEB_HOSTS = ("html.duckduckgo.com", "search.brave.com", "www.mojeek.com",
             "www.startpage.com", "search.yahoo.com", "www.google.com",
             "en.wikipedia.org", "grokipedia.com")
RESULT_CAP = 10
SNIPPET_CHARS = 200
WEB_TIMEOUT = 20        # seconds; the default of 5 is thin through a CONNECT proxy

# ddgs uses ONE exception type for both an empty result and a transport
# failure; this string is all that separates them.
_NO_RESULTS = "no results found"


@tool(risk="read")
def web_search(query: str, limit: int = 5) -> str:
    """Search the web for current information and return ranked results: title,
    URL and a one-line snippet. Use this when the answer depends on something
    outside the workspace - documentation, an error message, a library's current
    API. It returns links, NOT page contents: call fetch on a URL from these
    results when you need to read the page itself.

    query: What to search for, phrased as you would type it into a search engine.
    limit: How many results to return. Default 5, maximum 10.
    """
    # Imported here so this file stays importable without ddgs, and so a run that
    # never searches does not pay a Rust extension load.
    from ddgs import DDGS
    from ddgs.exceptions import DDGSException

    query = " ".join(str(query).split())
    if not query:
        raise ValueError("web_search needs a query. Pass what you would type into "
                         "a search engine, e.g. query='fastapi APIRouter post'.")
    count = max(1, min(_int(limit, 5), RESULT_CAP))

    # Passed EXPLICITLY: the openai SDK gets proxies from httpx's trust_env, but
    # ddgs goes through primp, which makes no such promise. Under a scored run an
    # ignored proxy is every search failing, not a slower one.
    try:
        rows = DDGS(proxy=os.environ.get("HTTPS_PROXY"), timeout=WEB_TIMEOUT).text(
            query, max_results=count)
    except DDGSException as exc:
        if _NO_RESULTS not in str(exc).lower():
            # The cause is NOT asserted: from inside the container a missing allowlist
            # entry and a refused connection are indistinguishable.
            raise RuntimeError(
                f"web search failed for {query!r}: {exc}. The search engines were "
                f"unreachable - egress is restricted to an allowlist, and it may "
                f"not carry them.") from exc
        rows = []

    if not rows:
        return (f"no results for {query!r}. Try fewer or more common words - this "
                f"searches the live web, so an exact phrase with no matches "
                f"returns nothing.")

    out = []
    for rank, row in enumerate(rows[:count], 1):
        snippet = " ".join((row.get("body") or "").split())[:SNIPPET_CHARS]
        out.append(f"{rank}. {row.get('title') or '(untitled)'}\n"
                   f"   {row.get('href') or ''}"
                   + (f"\n   {snippet}" if snippet else ""))
    return "\n".join(out)


# The schema is DERIVED from the signature and docstring above, so this is the
# whole registration. Order is deterministic: tools render first in the prompt.
TOOLS = {fn.__name__: fn.spec for fn in (
    read_file, search_files, write_file, edit_file, run_python, run_shell,
    web_search)}


def builtins() -> dict:
    """The built-ins exposed for THIS run - the same shape as memory.tools().

    web_search is dropped when AGENT_WEB is off, and that switch is what makes
    Stage 4's control run a controlled comparison rather than two numbers measured
    on different binaries. Gated here rather than by rebuilding TOOLS at import,
    so `policy.sync()` can still classify the tool and a test can flip the flag
    without reloading the module.
    """
    if config.WEB_ENABLED:
        return TOOLS
    return {name: entry for name, entry in TOOLS.items() if name != "web_search"}

SCHEMAS = [entry["schema"] for entry in TOOLS.values()]


def toolset() -> dict:
    """Kept as the name every caller already uses; the merge itself moved.

    Three modules contribute tools now (built-ins, MCP, memory), so who owns the
    merged view became a real question and `agent/registry.py` is the answer -
    §12's "add at tool six" trigger, fired by `remember`.
    """
    from agent.registry import toolset as merged

    return merged()
