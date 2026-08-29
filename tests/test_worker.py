"""The task queue and the worker (FR-601, FR-602, FR-603, FR-604).

The SEVENTH stated deviation from §12's three-file tests/ allowlist, to the same
standard as the others - the component's failure is SILENT. A queue that hands
one task to two workers, or that leaves a crashed worker's row saying `running`
forever, does not raise anything. It just quietly does the work twice, or never.
Neither shows up in another suite, and neither shows up in a pass rate.

No API key, no network: the graph is a stand-in throughout (NFR-602).
"""
import os
import time

import pytest

from agent import config, worker


@pytest.fixture
def queue(tmp_path, monkeypatch):
    """A task database of this test's own, never the real agent home."""
    monkeypatch.setattr(config, "TASKS_DB", tmp_path / "tasks.db")
    return tmp_path / "tasks.db"


# ===================================================================== FR-601

def test_submit_returns_an_id_immediately(queue):
    task_id = worker.submit("fix the failing tests")

    assert task_id and len(task_id) == 8
    row = worker.get(task_id)
    assert row["status"] == "queued"
    assert row["goal"] == "fix the failing tests"
    assert row["started_at"] is None


def test_the_id_is_the_thread_id(queue):
    """Not decoration: it is what makes resuming a task and resuming a thread the
    same operation, which is FR-603 for free."""
    task_id = worker.submit("goal")
    assert worker.get(task_id)["id"] == task_id


# ===================================================================== FR-604

def test_tasks_lists_newest_first(queue):
    first = worker.submit("first")
    time.sleep(0.01)
    second = worker.submit("second")

    assert [r["id"] for r in worker.tasks()] == [second, first]


def test_the_status_vocabulary_is_enforced_by_the_database(queue):
    """A CHECK constraint is the difference between a vocabulary and a
    suggestion. A typo in a transition must fail at the write, not create a sixth
    state nothing lists."""
    import sqlite3

    worker.submit("goal")
    with worker._connect() as conn:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("UPDATE tasks SET status='in-progress'")


def test_every_declared_status_is_actually_writable(queue):
    """The other direction: a CHECK that rejected a status the code uses would
    fail only in production."""
    import sqlite3

    with worker._connect() as conn:
        for status in worker.STATUSES:
            conn.execute("INSERT OR REPLACE INTO tasks "
                         "(id, goal, status, submitted_at) VALUES (?,?,?,?)",
                         (status, "g", status, time.time()))


# ============================================== transitions, applied exactly once

def test_claim_moves_one_task_to_running(queue):
    task_id = worker.submit("goal")

    claimed = worker.claim()

    assert claimed["id"] == task_id
    assert claimed["status"] == "running"
    assert claimed["pid"] == os.getpid()


def test_a_second_claim_finds_nothing(queue):
    """The idempotence that matters: two workers racing must produce one winner
    and one None, never the same task twice. NFR-302, in SQL."""
    worker.submit("goal")

    assert worker.claim() is not None
    assert worker.claim() is None


def test_claim_takes_the_oldest_first(queue):
    first = worker.submit("first")
    time.sleep(0.01)
    worker.submit("second")

    assert worker.claim()["id"] == first


def test_a_terminal_task_cannot_be_rewritten(queue):
    """A finished task is finished. Allowing a late write would let a slow
    worker overwrite the result of the one that actually did the job."""
    task_id = worker.submit("goal")
    worker.claim()

    assert worker.conclude(task_id, status="done", verdict="done") is not None
    assert worker.conclude(task_id, status="failed") is None
    assert worker.get(task_id)["status"] == "done"


def test_conclude_refuses_a_non_terminal_status(queue):
    task_id = worker.submit("goal")
    with pytest.raises(ValueError):
        worker.conclude(task_id, status="running")


# ============================================ liveness, and the recovery it enables

def test_a_live_worker_keeps_its_task(queue):
    worker.submit("goal")
    worker.claim()                       # claimed by THIS process, which is alive

    assert worker.recover() == 0
    assert worker.get(worker.tasks()[0]["id"])["status"] == "running"


def test_a_dead_workers_task_is_requeued(queue):
    """A crashed worker leaves a row saying `running` forever. Requeued rather
    than marked unknown - Hermes cannot retry because it does not know whether
    side effects ran; this project checkpoints after every node and CE-07 keeps
    gate and execute separate, so a resumed run re-classifies rather than
    re-executes."""
    task_id = worker.submit("goal")
    worker.claim()
    with worker._connect() as conn:
        conn.execute("UPDATE tasks SET pid=?, pid_started=? WHERE id=?",
                     (999_999, 1.0, task_id))

    assert worker.recover() == 1
    row = worker.get(task_id)
    assert row["status"] == "queued" and row["pid"] is None


def test_liveness_fails_safe_when_death_cannot_be_proven(queue):
    """Inability to prove death must not rewrite someone else's row. Assuming
    death when unsure is how the same task reaches two workers."""
    assert worker._alive(os.getpid(), None) is True
    assert worker._alive(os.getpid(), worker._pid_started(os.getpid())) is True
    assert worker._alive(None, None) is False


def test_a_recycled_pid_is_not_mistaken_for_the_original(queue):
    """Why the start time is stored at all. A pid alone cannot tell the original
    owner from whatever process inherited its number."""
    task_id = worker.submit("goal")
    worker.claim()
    with worker._connect() as conn:
        # Same pid, but it started at a different time - so it is a different
        # process wearing the same number.
        conn.execute("UPDATE tasks SET pid_started=? WHERE id=?", (1.0, task_id))

    assert worker.recover() == 1


# ===================================================================== FR-602

class FakeState:
    def __init__(self, values):
        self.values = values


class FakeGraph:
    """The graph's surface as the worker uses it: get_state and invoke."""

    def __init__(self, values=None, out=None, boom=None):
        self._values = values or {}
        self._out = out or {"verdict": "done", "denied": []}
        self._boom = boom
        self.invoked_with = []

    def get_state(self, cfg):
        return FakeState(dict(self._values))

    def invoke(self, payload, cfg):
        self.invoked_with.append(payload)
        if self._boom:
            raise self._boom
        return self._out


def test_the_worker_runs_a_task_to_done(queue):
    worker.submit("fix it")
    graph = FakeGraph()

    assert worker.run_worker(graph, once=True) == 1
    row = worker.tasks()[0]
    assert row["status"] == "done" and row["verdict"] == "done"


def test_an_empty_queue_is_not_an_error(queue):
    assert worker.run_worker(FakeGraph(), once=True) == 0


def test_a_crash_is_recorded_as_failed_rather_than_lost(queue):
    worker.submit("fix it")
    graph = FakeGraph(boom=RuntimeError("rate limited"))

    worker.run_worker(graph, once=True)

    row = worker.tasks()[0]
    assert row["status"] == "failed"
    assert "RuntimeError" in row["detail"] and "rate limited" in row["detail"]


# ===================================================================== FR-603

def test_a_requeued_task_resumes_rather_than_restarting(queue):
    """FR-603, and the reason requeueing is safe at all. A task whose thread
    already has messages must be invoked with None - which continues from the
    checkpoint - not with a fresh state, which would redo the work."""
    task_id = worker.submit("fix it")
    graph = FakeGraph(values={"messages": [{"role": "user", "content": "fix it"}]})

    worker.run_worker(graph, once=True)

    assert graph.invoked_with == [None], "a checkpointed thread must resume"
    assert worker.get(task_id)["status"] == "done"


def test_a_fresh_task_is_seeded_rather_than_resumed(queue):
    worker.submit("fix it")
    graph = FakeGraph(values={})

    worker.run_worker(graph, once=True)

    seeded = graph.invoked_with[0]
    assert seeded is not None
    assert seeded["messages"][0]["content"] == "fix it"


# ====================================================== UR-16, awaiting-approval

def test_a_run_that_refused_something_ends_awaiting_approval(queue):
    """`autonomous=True` turns a `confirm` into a denial (FR-304), so nothing
    pauses - nobody is watching. But a run that REFUSED destructive calls is not
    simply done: UR-16 asks to review what was queued while you were away, and
    filing that under `done` makes it unanswerable."""
    worker.submit("clean the build")
    graph = FakeGraph(out={"verdict": "done",
                           "denied": [{"name": "run_shell", "reason": "destructive"}]})

    worker.run_worker(graph, once=True)

    row = worker.tasks()[0]
    assert row["status"] == "awaiting-approval"
    assert "run_shell" in row["detail"]


def test_a_clean_run_is_not_flagged_for_review(queue):
    worker.submit("read a file")
    worker.run_worker(FakeGraph(out={"verdict": "done", "denied": []}), once=True)

    assert worker.tasks()[0]["status"] == "done"


def test_a_stuck_agent_is_a_failed_task(queue):
    """`done` means the AGENT finished the job, not merely that the worker
    stopped running it. Filing a stuck run under `done` would make the status
    column useless - you would have to read the verdict to learn nothing was
    achieved."""
    worker.submit("fix it")
    worker.run_worker(FakeGraph(out={"verdict": "stuck", "denied": []}), once=True)

    row = worker.tasks()[0]
    assert row["status"] == "failed"
    assert row["verdict"] == "stuck", "the verdict is kept either way"


def test_a_budget_exhausted_agent_is_also_failed(queue):
    worker.submit("fix it")
    worker.run_worker(FakeGraph(out={"verdict": "budget", "denied": []}), once=True)

    assert worker.tasks()[0]["status"] == "failed"
