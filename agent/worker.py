"""The task queue and the worker that drains it (FR-601, FR-602, FR-604).

§12 deferred this file with the trigger "when FR-6xx enters scope". It fires
here: the three requirements are `[M]`, and §9 puts scope in play once the `[M]`
set passes evaluation, which it has.

WHAT THIS IS NOT. Hermes's equivalent is `cron/scheduler.py`, 7,644 lines welded
to a 13,732-line state module, and none of it can be lifted. What ports is
`cron/executions.py` - 284 lines of stdlib and SQLite - and specifically two
ideas from it, both of which are about not lying about state:

  IDEMPOTENT TRANSITIONS.  `UPDATE ... WHERE id=? AND status='queued'` followed
  by `if cur.rowcount != 1: return None`. A transition applies exactly once and a
  second attempt DECLINES rather than corrupting the row. That is NFR-302 - no
  duplicated side effects on resume - expressed in SQL, and it is the same
  discipline CE-07 enforces in the graph.

  LIVENESS BY pid AND START TIME.  A crashed worker leaves a row saying
  `running` forever. A pid alone cannot detect that, because pids are recycled
  and the next process to claim one would look like the original. The pair
  cannot be.

WHERE THIS DELIBERATELY DIVERGES FROM HERMES. It marks an abandoned execution
`unknown` and refuses to retry, on the ground that "whether side effects ran is
unknown". That is correct for Hermes and wrong here: this project checkpoints
after every node transition and CE-07 keeps `gate` and `execute` separate
precisely so a resumed run cannot re-fire a tool. An abandoned task therefore
goes back to `queued`, and the worker that picks it up RESUMES from the
checkpoint rather than restarting - which is FR-603, and the reason it is safe.

CE-05: nothing here runs at import; the database is created on first use.
NFR-602: every function below is testable with no API key and no network.
"""
from __future__ import annotations

import os
import sqlite3
import time
import uuid

from agent import config

# FR-604's five states, enforced by SQLite rather than by convention: a CHECK
# constraint refuses a typo that a Python-side check would let through.
STATUSES = ("queued", "running", "awaiting-approval", "done", "failed")

SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    id           TEXT PRIMARY KEY,
    goal         TEXT NOT NULL,
    status       TEXT NOT NULL CHECK(status IN
                   ('queued','running','awaiting-approval','done','failed')),
    verdict      TEXT,
    detail       TEXT,
    submitted_at REAL NOT NULL,
    started_at   REAL,
    finished_at  REAL,
    pid          INTEGER,
    pid_started  REAL
);
CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status, submitted_at);
"""


def _connect() -> sqlite3.Connection:
    config.TASKS_DB.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(config.TASKS_DB), isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=5000")
    conn.executescript(SCHEMA)
    return conn


def _pid_started(pid: int) -> float | None:
    """When `pid` started, or None when that cannot be determined.

    Read from /proc, which is where the worker runs. None on any other platform,
    and the liveness check below treats None as "cannot prove death".
    """
    try:
        with open(f"/proc/{pid}/stat", encoding="utf-8") as handle:
            return float(handle.read().rsplit(")", 1)[1].split()[19])
    except (OSError, IndexError, ValueError):
        return None


def _alive(pid: int | None, started: float | None) -> bool:
    """Whether the process that claimed a task is still running.

    FAILS SAFE: inability to prove death must not rewrite someone else's row.
    Hermes's rule, and the reason is that the alternative - assuming death when
    unsure - hands the same task to two workers.
    """
    if pid is None:
        return False
    # No fast path for "that is my own pid": it would skip the start-time compare,
    # which is the only thing distinguishing the owner from a recycled pid.
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except (PermissionError, OSError):
        return True                      # exists, or cannot be interrogated
    if started is None:
        return True                      # cannot compare, so cannot prove death
    now = _pid_started(pid)
    return now is None or now == started


def _row(record: sqlite3.Row | None) -> dict | None:
    return dict(record) if record is not None else None


# ------------------------------------------------------------------ FR-601

def submit(goal: str) -> str:
    """Accept a task and return its identifier immediately (FR-601).

    The id IS the graph's thread id, which is what makes FR-603 free: resuming a
    task and resuming a thread are the same operation.
    """
    task_id = uuid.uuid4().hex[:8]
    with _connect() as conn:
        conn.execute(
            "INSERT INTO tasks (id, goal, status, submitted_at) VALUES (?,?,?,?)",
            (task_id, goal, "queued", time.time()))
    return task_id


# ------------------------------------------------------------------ FR-604

def tasks(limit: int = 50) -> list[dict]:
    """Every task, newest first (FR-604)."""
    with _connect() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM tasks ORDER BY submitted_at DESC LIMIT ?", (limit,))]


def get(task_id: str) -> dict | None:
    with _connect() as conn:
        return _row(conn.execute(
            "SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone())


# ------------------------------------------- transitions, each applied once

def claim() -> dict | None:
    """Move one queued task to running, or return None.

    The `AND status='queued'` in the UPDATE is the whole concurrency story: two
    workers racing for the same row produce one winner and one None, because
    SQLite settles it rather than the reader-then-writer sequence that would
    hand it to both.

    Returns None when MAX_WORKERS are already running (FR-607).
    """
    pid = os.getpid()
    # FR-607, and the ORDER matters. A `running` row whose worker died still
    # counts against the cap, so without this a single crash deadlocks the
    # queue permanently. recover() is the existing liveness sweep.
    recover()
    with _connect() as conn:
        running = conn.execute(
            "SELECT COUNT(*) AS n FROM tasks WHERE status='running'").fetchone()
        if running["n"] >= config.MAX_WORKERS:
            return None
        row = conn.execute(
            "SELECT id FROM tasks WHERE status='queued' "
            "ORDER BY submitted_at LIMIT 1").fetchone()
        if row is None:
            return None
        cur = conn.execute(
            "UPDATE tasks SET status='running', started_at=?, pid=?, pid_started=? "
            "WHERE id=? AND status='queued'",
            (time.time(), pid, _pid_started(pid), row["id"]))
        if cur.rowcount != 1:
            return None                  # someone else took it between the two
        return _row(conn.execute(
            "SELECT * FROM tasks WHERE id=?", (row["id"],)).fetchone())


def conclude(task_id: str, *, status: str, verdict: str = "",
             detail: str = "") -> dict | None:
    """Write a terminal state once. A task already finished cannot be rewritten."""
    if status not in ("done", "failed", "awaiting-approval"):
        raise ValueError(f"{status!r} is not a terminal status")
    with _connect() as conn:
        cur = conn.execute(
            "UPDATE tasks SET status=?, verdict=?, detail=?, finished_at=? "
            "WHERE id=? AND status IN ('running','queued')",
            (status, verdict, detail, time.time(), task_id))
        if cur.rowcount != 1:
            return None
        return _row(conn.execute(
            "SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone())


def recover() -> int:
    """Requeue tasks whose worker died. Returns how many.

    Hermes marks these `unknown` and does NOT retry, because it cannot know
    whether side effects ran. This project can: state is checkpointed after every
    node transition, and CE-07 keeps `gate` and `execute` separate so a resumed
    run re-classifies rather than re-executes. So the safe move here is `queued`,
    and the worker that picks it up resumes mid-task (FR-603).
    """
    changed = 0
    with _connect() as conn:
        for row in conn.execute(
                "SELECT id, pid, pid_started FROM tasks WHERE status='running'"):
            if _alive(row["pid"], row["pid_started"]):
                continue
            changed += conn.execute(
                "UPDATE tasks SET status='queued', pid=NULL, pid_started=NULL "
                "WHERE id=? AND status='running'", (row["id"],)).rowcount
    return changed


# ------------------------------------------------------------------ FR-602

def run_once(app, task: dict, trace: list | None = None) -> dict | None:
    """Run one claimed task to a terminal state.

    `autonomous=True`, so a `confirm` verdict becomes a denial (FR-304) rather
    than a pause. That is not a compromise, it is the only coherent reading:
    nobody is watching, and a worker that suspended on approval would hold the
    task open until someone happened to look.

    Which is what `awaiting-approval` is FOR here. A run that finished having
    REFUSED destructive calls is not simply `done` - UR-16 asks to review what
    was queued while you were away, and a task that hid its refusals under
    `done` would make that unanswerable.
    """
    from agent.graph import new_state

    cfg = {"configurable": {"thread_id": task["id"], "autonomous": True,
                            "trace": trace if trace is not None else []}}
    prior = app.get_state(cfg).values or {}
    try:
        # None resumes a checkpointed thread; a fresh state seeds a new one. A
        # requeued task takes the first branch, which is FR-603 in one line.
        out = app.invoke(None if prior.get("messages") else new_state(task["goal"]), cfg)
    except Exception as exc:              # noqa: BLE001 - a crash is a result
        return conclude(task["id"], status="failed",
                        detail=f"{type(exc).__name__}: {exc}")

    verdict = out.get("verdict") or ""
    denied = out.get("denied") or []
    if denied:
        names = ", ".join(sorted({c.get("name", "?") for c in denied}))
        return conclude(task["id"], status="awaiting-approval", verdict=verdict,
                        detail=f"refused while unattended: {names}")

    # `done` means the AGENT finished, not that the worker exited cleanly - a
    # crashed worker leaves the task running so recover() can requeue it.
    if verdict == "done":
        return conclude(task["id"], status="done", verdict=verdict)
    return conclude(task["id"], status="failed", verdict=verdict,
                    detail=f"agent ended {verdict or 'with no verdict'}")


def run_worker(app, once: bool = False, poll: float = 2.0) -> int:
    """Drain the queue, one task at a time (FR-602).

    A loop over the graph this project already has, not a supervisor: ~40 lines
    against Hermes's 7,644, because everything hard about running a task -
    checkpointing, the gate, budgets - is already in the graph.
    """
    recover()
    while True:
        task = claim()
        if task is None:
            if once:
                return 0
            time.sleep(poll)
            continue
        run_once(app, task)
        if once:
            return 1
