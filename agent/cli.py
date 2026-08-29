"""Interactive entrypoint (FR-701, FR-703).

    python -m agent "goal"           run a new session
    python -m agent --list           show past threads
    python -m agent --resume <id>    continue one

The first place a human is in the loop. Every run before this point was
unattended, where autonomous=True turns a `confirm` verdict into a refusal and
nothing ever pauses. Interactive mode sets it False, which is the switch that
makes the gate suspend and ask instead - so this file is the first caller of a
code path that had never executed.

Output is ASCII on purpose: this runs in a container whose output lands in a
Windows terminal, and box-drawing characters arrive there as mojibake often
enough that they are not worth the risk.
"""
from __future__ import annotations

import argparse
import sys
import uuid

from langgraph.types import Command

from agent import config as settings
from agent import mcp, memory, skills
from agent.graph import get_app, new_state

RULE = "-" * 64

# Returned by the prompt to stop the session rather than answer the question.
# Distinct from "deny", which answers it: the gate treats every non-"allow"
# string as a rejection, so this must never reach it.
QUIT = "\0quit"


# --------------------------------------------------------------- live display

class LiveTrace(list):
    """A trace list that renders as it fills.

    Every node already reports through `trace.append(...)`, so subclassing that
    list is the entire live-output mechanism: graph.py needs no callback, no
    streaming API, and no knowledge that a terminal exists. The harness keeps
    passing a plain list and is unaffected.
    """

    def __init__(self, sink=None) -> None:
        super().__init__()
        self.turn = 0
        self.tokens = 0
        # Only the RENDERING is swappable. The counters are not, because the TUI
        # needs the same turn and token totals this printer does - so they live
        # in append(), where every sink gets them, rather than inside one
        # particular way of showing them.
        self._sink = sink or self._render

    def append(self, entry: dict) -> None:
        super().append(entry)
        if entry.get("kind") == "model":
            # Turn numbers are counted here rather than read from state: a turn
            # begins when the model is called, and the caller sees that moment
            # live while state only arrives once invoke() returns.
            self.turn += 1
            self.tokens += entry.get("billed_tokens", 0)
        self._sink(entry)

    def _render(self, e: dict) -> None:
        kind = e.get("kind")
        if kind == "model":
            print(f"\nturn {self.turn}/{settings.MAX_TURNS}"
                  f"   {self.tokens:,} tokens", flush=True)
        elif kind == "tool":
            print("  " + _tool_line(e), flush=True)
        elif kind == "terminal":
            print(f"\n{RULE}\n{e['verdict']}  |  {e['turns']} turns  |  "
                  f"{e['spent_tokens']:,} tokens", flush=True)


def _tool_line(e: dict) -> str:
    """One line per tool call: what ran, on what, how it went, how long."""
    mark = "x" if e.get("is_error") else ">"
    note = "  DENIED" if e.get("verdict") == "deny" else ("  ERROR" if e.get("is_error") else "")
    if e.get("spill_path"):
        note += "  [spilled]"
    return (f"{mark} {e['tool']:<11} {e.get('summary', '')[:42]:<42}"
            f"{e.get('duration_ms', 0) / 1000:>6.1f}s{note}")


def _on_text(text: str) -> None:
    """Model prose as it arrives.

    Written incrementally because the two providers deliver it differently: the
    Anthropic path streams deltas, the OpenAI-compatible path hands over one
    finished block. Plain appending is correct for both.
    """
    sys.stdout.write(text)
    sys.stdout.flush()


# ------------------------------------------------------------------- approval

def ask_plan(payload: dict) -> str:
    """Show the plan and read one keystroke (UR-02, UR-05).

    Same contract as the tool prompt below: an unrecognised key re-asks, never
    guesses. What differs is the safe answer with no terminal attached. There it
    is `deny` - a refusal the agent routes around and the run continues. Here it
    is QUIT, because `revise` would send the graph back to planning and ask
    again, and with nothing on stdin that is an infinite loop rather than a
    safe default.
    """
    print(f"\n  +-- PLAN {'-' * 53}")
    for number, step in enumerate(payload["plan"], 1):
        print(f"  | {number}. {step}")
    print(f"  +{'-' * 61}")

    while True:
        try:
            answer = input("    [a]ccept  [r]evise  [q]uit > ").strip().lower()
        except EOFError:
            print("quit (no terminal)")
            return QUIT
        if answer in ("a", "accept"):
            return "accept"
        if answer in ("r", "revise"):
            try:
                note = input("    what should change? > ").strip()
            except EOFError:
                note = ""
            # The note travels back as an ordinary user message, so saying WHY
            # is worth a second prompt; an empty answer still revises.
            return note or "revise"
        if answer in ("q", "quit"):
            return QUIT
        print("    unrecognised - answer a, r or q")


def ask_human(payload: dict) -> str:
    """Render a paused call and read one keystroke (FR-306, NFR-801).

    The argument set is shown in full, never abbreviated. A prompt that elides
    the dangerous half of a command manufactures consent instead of obtaining it.
    """
    if "plan" in payload:
        return ask_plan(payload)

    call = payload["call"]
    print(f"\n  +-- APPROVAL NEEDED {'-' * 42}")
    print(f"  | {call['name']}")
    print("  |")
    for key, value in call["input"].items():
        print(f"  |   {key}: {value}")
    print("  |")
    print(f"  | Reason: {payload.get('reason', '')}")
    print(f"  +{'-' * 61}")

    while True:
        try:
            answer = input("    [a]llow  [d]eny  [q]uit > ").strip().lower()
        except EOFError:
            # No terminal attached. Silence is not consent.
            print("deny (no terminal)")
            return "deny"
        if answer in ("a", "allow"):
            return "allow"
        if answer in ("d", "deny"):
            return "deny"
        if answer in ("q", "quit"):
            return QUIT
        # Anything unrecognised re-asks. A mistyped key must never read as yes.
        print("    unrecognised - answer a, d or q")


# -------------------------------------------------------------------- session

def run_session(goal: str | None, thread: str, app) -> int:
    """One interactive run, from a fresh goal or a resumed thread.

    A single loop shape covers both, plus every approval pause, because resuming
    a thread and resuming an interrupt are the same operation to the graph: a
    task's identity IS its thread id.
    """
    trace = LiveTrace()
    cfg = {"configurable": {
        "thread_id": thread,
        "autonomous": False,      # the switch that makes `confirm` pause, not refuse
        "trace": trace,
        "on_text": _on_text,
    }}

    print(f"thread  {thread}")
    if goal:
        print(f"goal    {goal}")
    else:
        # Resuming: seed the live counters from the checkpoint, or the display
        # would restart at turn 1 while the run is really on turn 9.
        prior = app.get_state(cfg).values or {}
        trace.turn, trace.tokens = prior.get("turns", 0), prior.get("spent_tokens", 0)
        messages = prior.get("messages") or []
        if messages and isinstance(messages[0].get("content"), str):
            print(f"goal    {messages[0]['content']}")
        print(f"resumed at turn {trace.turn}, {trace.tokens:,} tokens spent")
    print(RULE)

    # None as the payload continues an existing thread instead of seeding one.
    out = app.invoke(new_state(goal) if goal is not None else None, cfg)

    while "__interrupt__" in out:
        answer = ask_human(out["__interrupt__"][0].value)
        if answer == QUIT:
            # A half-approved turn is a legitimate resting place. The checkpoint
            # already holds it, so quitting costs nothing but the prompt.
            print(f"\nstopped, still checkpointed.\n"
                  f"resume with:  python -m agent --resume {thread}")
            return 0
        out = app.invoke(Command(resume=answer), cfg)

    print(f"resume with:  python -m agent --resume {thread}")
    return 0


# -------------------------------------------------------------------- threads

def _thread_ids(app) -> list[str]:
    """Distinct thread ids, newest first, through the app's own checkpointer
    rather than a second connection to the same database."""
    seen: list[str] = []
    for tup in app.checkpointer.list(None):
        tid = tup.config.get("configurable", {}).get("thread_id")
        if tid and tid not in seen:
            seen.append(tid)
    return seen


def _thread_rows(app) -> list[dict]:
    """Thread id, verdict, turn count and goal - newest first, as DATA.

    The TUI puts this same walk in a table while list_threads() prints it. One
    traversal of the checkpointer with two presentations, rather than two
    traversals that can quietly disagree about what a thread's goal was.
    """
    rows = []
    for tid in _thread_ids(app):
        values = app.get_state({"configurable": {"thread_id": tid}}).values or {}
        messages = values.get("messages") or []
        goal = messages[0]["content"] if messages and isinstance(
            messages[0].get("content"), str) else ""
        rows.append({"id": tid, "verdict": values.get("verdict") or "-",
                     "turns": values.get("turns", 0), "goal": goal})
    return rows


def list_threads(app) -> int:
    rows = _thread_rows(app)
    if not rows:
        print("no threads yet")
        return 0
    print(f"{'thread':<20} {'verdict':<10} {'turns':>5}  goal")
    for row in rows:
        print(f"{row['id']:<20} {row['verdict']:<10} "
              f"{row['turns']:>5}  {row['goal'][:40]}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m agent")
    parser.add_argument("goal", nargs="?", help="what to do, in plain language")
    parser.add_argument("--list", action="store_true", help="show past threads")
    parser.add_argument("--resume", metavar="THREAD_ID", help="continue a thread")
    parser.add_argument("--tui", action="store_true",
                        help="full-screen chat (FR-701) instead of line output")
    parser.add_argument("--submit", metavar="GOAL",
                        help="queue a task and print its id, without running it")
    parser.add_argument("--worker", action="store_true",
                        help="drain the task queue; runs until interrupted")
    parser.add_argument("--tasks", action="store_true",
                        help="show queued and finished tasks")
    args = parser.parse_args(argv)

    app = get_app()

    if args.list:
        return list_threads(app)          # no model, so no tools, so no startup

    # Started before EITHER path that calls a model, and torn down in `finally` so a
    # server subprocess never outlives the session that asked for it. --resume needs
    # it as much as a fresh goal does: a thread that used an MCP tool and is resumed
    # without one would have that tool refused as unknown, mid-task.
    memory.activate()
    try:
        loaded = skills.activate()
    except skills.SkillIndexTooLarge as exc:
        print(f"skills: {exc}", file=sys.stderr)
        return 2
    if loaded:
        print(f"skills available: {', '.join(loaded)}")
    try:
        started = mcp.activate()
    except (mcp.McpUnavailable, mcp.ToolBudgetExceeded) as exc:
        print(f"MCP: {exc}", file=sys.stderr)
        print("Run with AGENT_MCP=off to continue with the built-in tools only.",
              file=sys.stderr)
        return 2
    if started:
        print(f"MCP tools: {', '.join(started)}")

    try:
        return _dispatch(args, app, parser)
    except KeyboardInterrupt:
        # Everything is checkpointed after every node transition, so an interrupt
        # costs at most one node. Saying so beats a traceback.
        print("\nstopped. resume with:  python -m agent --list")
        return 0
    finally:
        try:
            mcp.shutdown()
        except KeyboardInterrupt:
            # A SECOND ctrl-c, landing inside the transport thread's join. The
            # subprocess dies with the container either way; a stack trace here
            # only makes a clean exit look like a failure.
            pass
        memory.deactivate()
        skills.deactivate()


def list_tasks() -> int:
    """FR-604, and the other half of FR-703's "list threads and tasks"."""
    from agent import worker

    rows = worker.tasks()
    if not rows:
        print("no tasks yet")
        return 0
    print(f"{'task':<10} {'status':<18} {'verdict':<9} goal")
    for row in rows:
        print(f"{row['id']:<10} {row['status']:<18} {row['verdict'] or '-':<9} "
              f"{row['goal'][:38]}")
        if row.get("detail"):
            # UR-16: what it refused while nobody was watching is the whole
            # reason to look at this list.
            print(f"{'':<10} ! {row['detail'][:60]}")
    return 0


def _dispatch(args, app, parser) -> int:
    from agent import worker

    if args.tasks:
        return list_tasks()

    if args.submit:
        # FR-601: returns immediately. The id IS the thread id, which is what
        # makes resuming a task and resuming a thread the same operation.
        task_id = worker.submit(args.submit)
        print(f"queued {task_id}")
        print("run it with:   python -m agent --worker")
        print("watch it with: python -m agent --tasks")
        return 0

    if args.worker:
        requeued = worker.recover()
        if requeued:
            print(f"requeued {requeued} task(s) whose worker had died")
        print("worker running; ctrl-c to stop")
        worker.run_worker(app)
        return 0

    if args.tui:
        # Imported HERE, never at module top: the plain CLI, the eval harness and
        # the unit suite must all keep running where textual is not installed
        # (NFR-602), and CE-05 forbids settling at import time what belongs at
        # the call site. main() has already activated memory, skills and MCP, so
        # the TUI inherits the same lifecycle - and the same `finally` teardown.
        from agent import tui
        return tui.run(app, goal=args.goal, thread=args.resume)

    if args.resume:
        if args.resume not in _thread_ids(app):
            # Say what exists rather than raising a traceback at someone who
            # mistyped eight hex characters.
            print(f"no such thread: {args.resume}\n", file=sys.stderr)
            list_threads(app)
            return 2
        return run_session(None, args.resume, app)

    if not args.goal:
        parser.error("give a goal, or use --list / --resume")
    return run_session(args.goal, uuid.uuid4().hex[:8], app)
