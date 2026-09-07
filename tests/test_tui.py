"""TUI: the approval modal, and the thread that drives it.

The sixth stated deviation from the three-file tests/ allowlist, on exactly the
ground test_cli.py was justified on - the approval prompt is where consent is
decided. The TUI is not a second copy of that risk, it is a NEW one: a modal has
dismissal paths a keystroke loop cannot have (escape, click-away, a closed
window), and a modal that answers "allow" when it is dismissed is a security
defect that manual testing finds only by accident.

Driven through Textual's own headless pilot, so these run with no terminal, no
API key and no network, like every other suite here (NFR-602). `asyncio.run`
rather than pytest-asyncio: the pilot is an async context manager, and one
`asyncio.run` per test is cheaper than a new test dependency.
"""
import asyncio

import pytest

from textual.app import App
from textual.widgets import DataTable

from agent import cli
from agent import worker
from agent.ui.modals import ApprovalScreen, PlanScreen
from agent.ui.panes import (DoctorPane, SchedulesPane, TasksPane,
                            doctor_text as _doctor_text,
                            tool_text as _tool_text)
from agent.ui.screens import COMMANDS, NoesisApp, WorkspaceScreen


PAUSE = {"call": {"id": "t1", "name": "run_shell",
                  "input": {"command": "rm -rf build", "timeout": 120}},
         "reason": "run_shell classified destructive"}


def drive(app, script):
    """Run `script(pilot)` against a headless app and return control."""
    async def _go():
        async with app.run_test() as pilot:
            await pilot.pause()
            await script(pilot)
            await pilot.pause()
    asyncio.run(_go())


class Asked(App):
    """Pushes the modal once and keeps whatever it answers."""

    def __init__(self, payload=PAUSE) -> None:
        super().__init__()
        self._payload = payload
        self.answer = "UNSET"

    def on_mount(self) -> None:
        self.push_screen(ApprovalScreen(self._payload),
                         callback=lambda result: setattr(self, "answer", result))


# ==================================================================== approval

def test_escape_is_deny_never_allow():
    """The security test. A modal can be dismissed in ways a keystroke loop
    cannot, and every one of them must refuse."""
    app = Asked()

    async def script(pilot):
        await pilot.press("escape")

    drive(app, script)
    assert app.answer == "deny"


@pytest.mark.parametrize("button,expected",
                         [("#allow", "allow"), ("#deny", "deny"), ("#quit", cli.QUIT)])
def test_each_button_returns_its_own_answer(button, expected):
    app = Asked()

    async def script(pilot):
        await pilot.click(button)

    drive(app, script)
    assert app.answer == expected


def test_quit_is_distinct_from_deny():
    """Quitting stops the session; denying answers the question and continues.
    Collapsing them turns a walk-away into a policy decision - the same contract
    test_cli.py pins for the line-mode prompt."""
    assert cli.QUIT not in ("deny", "allow")


def test_enter_refuses_rather_than_approves():
    """Deny holds focus on mount, so the reflex key is the safe one. The
    dangerous answer has to be chosen deliberately."""
    app = Asked()

    async def script(pilot):
        await pilot.press("enter")

    drive(app, script)
    assert app.answer == "deny"


def test_every_argument_is_shown_in_full():
    """FR-306: a prompt that elides the dangerous half of a command manufactures
    consent. `rm -rf build` must appear whole, not truncated to `rm -rf ...`."""
    seen = []
    app = Asked()

    async def script(pilot):
        # `.content` rather than `.renderable`: Textual 8 renders through Visuals,
        # and Label subclasses Static so one query covers both.
        seen.append(" ".join(
            getattr(node.content, "plain", str(node.content))
            for node in pilot.app.screen.query("Static, Label")))
        await pilot.press("escape")

    drive(app, script)
    assert "rm -rf build" in seen[0]
    assert "timeout: 120" in seen[0]


# ====================================================================== worker

class FakeState:
    def __init__(self, values):
        self.values = values


class FakeGraph:
    """The graph's surface as the TUI uses it: get_state and invoke.

    Reporting through `cfg["trace"]` is the real contract - graph.py knows
    nothing about terminals and appends to a list. Whether that list's entries
    survive the hop from a worker thread to a widget is what this exercises.
    """

    def __init__(self, values=None):
        self._values = values or {}

    def get_state(self, cfg):
        return FakeState(dict(self._values))

    def invoke(self, payload, cfg):
        trace = cfg["configurable"]["trace"]
        cfg["configurable"]["on_text"]("hello from the model")
        trace.append({"kind": "tool", "tool": "read_file",
                      "summary": "ledger/parser.py", "duration_ms": 12})
        trace.append({"kind": "model", "billed_tokens": 1_234})
        trace.append({"kind": "terminal", "verdict": "done", "turns": 1,
                      "spent_tokens": 1_234})
        return {}


def transcript(app) -> str:
    """The chat pane's log. The transcript itself lives on the screen now, and
    the pane is a view over it - either would do; this reads what a person
    would actually see."""
    log = app.screen.query_one("#chat-log")
    return "\n".join(strip.text for strip in log.lines)


def test_a_trace_entry_from_the_worker_thread_reaches_the_transcript():
    """The mechanism the whole screen rests on: the graph runs on a thread and
    every line it produces crosses back through call_from_thread. If this hop is
    broken the UI simply stops moving, with no error anywhere."""
    app = NoesisApp(FakeGraph(), goal="fix the tests", thread="t1")
    text = []

    async def script(pilot):
        await pilot.app.screen.workers.wait_for_complete()
        await pilot.pause()
        text.append(transcript(pilot.app))

    drive(app, script)
    assert "read_file" in text[0]
    assert "ledger/parser.py" in text[0]
    assert "done" in text[0] and "1,234 tokens" in text[0]


def test_streamed_text_is_buffered_then_flushed_as_one_block():
    """Anthropic streams deltas, the OpenAI path hands over one finished block.
    A log line per delta would shred a sentence into one word per row, so the
    buffer renders live and moves into the log when the turn completes."""
    app = NoesisApp(FakeGraph(), goal="fix the tests", thread="t1")
    text = []

    async def script(pilot):
        await pilot.app.screen.workers.wait_for_complete()
        await pilot.pause()
        text.append(transcript(pilot.app))
        # The live buffer is emptied once flushed, or the next turn would
        # re-render everything the previous one already said.
        assert pilot.app.screen._stream == ""

    drive(app, script)
    assert "hello from the model" in text[0]


def test_a_provider_error_is_shown_rather_than_swallowed():
    """Textual sends an unhandled worker exception to a devtools console nobody
    is watching; on screen the run would just stop moving."""
    class Broken(FakeGraph):
        def invoke(self, payload, cfg):
            raise RuntimeError("rate limited")

    app = NoesisApp(Broken(), goal="fix the tests", thread="t1")
    text = []

    async def script(pilot):
        await pilot.app.screen.workers.wait_for_complete()
        await pilot.pause()
        text.append(transcript(pilot.app))

    drive(app, script)
    assert "RuntimeError" in text[0] and "rate limited" in text[0]


# ==================================================================== renderer

def test_tool_line_marks_denied_and_errored_calls_differently():
    """Three outcomes, three marks. A denied call is not a failed one, and
    collapsing them hides the gate doing its job."""
    ok = _tool_text({"tool": "read_file", "summary": "a.py", "duration_ms": 10})
    bad = _tool_text({"tool": "read_file", "summary": "a.py", "duration_ms": 10,
                      "is_error": True})
    denied = _tool_text({"tool": "run_shell", "summary": "rm -rf /", "duration_ms": 0,
                         "verdict": "deny"})
    # Section 8 gives denied and errored the same glyph, so the WORD is what
    # keeps them apart - and it must, because a denied call is not a failed one
    # and collapsing them hides the gate doing its job.
    assert ok.plain.strip().startswith("◇")
    assert "error" in bad.plain and bad.plain.strip().startswith("✕")
    assert "denied" in denied.plain and denied.plain.strip().startswith("✕")
    assert "denied" not in bad.plain and "error" not in denied.plain


def test_tool_summary_is_not_parsed_as_console_markup():
    """Summaries carry arbitrary file content. A `[/]` in someone's code must
    render as `[/]`, which is why the log has markup off and every write is a
    rich renderable rather than a string."""
    line = _tool_text({"tool": "read_file", "summary": "x = a[/]b",
                       "duration_ms": 10})
    assert "[/]" in line.plain


# ======================================================== the plan modal

PLAN_PAUSE = {"plan": ["Read tests/test_export.py",
                       "Add a CSV writer to ledger/export.py",
                       "Run pytest -q until green"],
              "reason": "plan ready for review"}


class AskedPlan(App):
    def __init__(self, payload=PLAN_PAUSE) -> None:
        super().__init__()
        self._payload = payload
        self.answer = "UNSET"

    def on_mount(self) -> None:
        self.push_screen(PlanScreen(self._payload),
                         callback=lambda result: setattr(self, "answer", result))


def test_dismissing_the_plan_revises_it_rather_than_adopting_it():
    """The same rule as the tool modal's deny-on-dismissal: an approval nobody
    gave must not be manufactured by a stray escape."""
    app = AskedPlan()

    async def script(pilot):
        await pilot.press("escape")

    drive(app, script)
    assert app.answer == "revise"


@pytest.mark.parametrize("button,expected",
                         [("#accept", "accept"), ("#revise", "revise"),
                          ("#quit", cli.QUIT)])
def test_each_plan_button_returns_its_own_answer(button, expected):
    app = AskedPlan()

    async def script(pilot):
        await pilot.click(button)

    drive(app, script)
    assert app.answer == expected


def test_enter_accepts_here_because_nothing_has_been_changed_yet():
    """The opposite default from the tool modal, on purpose. There the reflex
    key must refuse, because allowing runs a command. Here the reflex key is
    harmless - accepting a plan writes nothing, and every step still passes the
    gate one at a time."""
    app = AskedPlan()

    async def script(pilot):
        await pilot.press("enter")

    drive(app, script)
    assert app.answer == "accept"


def test_the_plan_modal_shows_every_step():
    seen = []
    app = AskedPlan()

    async def script(pilot):
        seen.append(" ".join(
            getattr(node.content, "plain", str(node.content))
            for node in pilot.app.screen.query("Static, Label")))
        await pilot.press("escape")

    drive(app, script)
    for step in PLAN_PAUSE["plan"]:
        assert step in seen[0]


def test_the_header_shows_the_active_step():
    """FR-702, which had no answer at all until the plan node landed."""
    graph = FakeGraph({"plan": ["read it", "fix it", "test it"], "cursor": 1,
                       "phase": "working", "turns": 4, "max_turns": 12,
                       "spent_tokens": 18_402})
    app = NoesisApp(graph, goal=None, thread="9f2a1c")
    titles = []

    async def script(pilot):
        await pilot.pause()
        titles.append(status_text(pilot.app))

    drive(app, script)
    assert "2/3" in titles[0] and "fix it" in titles[0]
    assert "4/12" in titles[0] and "18,402" in titles[0]


def test_the_header_says_planning_before_a_plan_exists():
    graph = FakeGraph({"plan": [], "cursor": 0, "phase": "planning",
                       "turns": 0, "max_turns": 12, "spent_tokens": 0})
    app = NoesisApp(graph, goal=None, thread="9f2a1c")
    titles = []

    async def script(pilot):
        await pilot.pause()
        titles.append(status_text(pilot.app))

    drive(app, script)
    assert "planning" in titles[0]


def test_the_step_survives_every_layout_including_zoom():
    """FR-702 says at ALL times. A pane can be closed or zoomed over; the
    status bar cannot, which is why the step lives there."""
    graph = FakeGraph({"plan": ["read it", "fix it"], "cursor": 0,
                       "phase": "working", "turns": 1, "max_turns": 12,
                       "spent_tokens": 10})
    app = NoesisApp(graph, goal=None, thread="9f2a1c")
    seen = []

    async def script(pilot):
        await pilot.pause()
        seen.append(status_text(pilot.app))
        await pilot.press("alt+enter")
        await pilot.pause()
        seen.append(status_text(pilot.app))
        await pilot.press("alt+z")
        await pilot.pause()
        seen.append(status_text(pilot.app))

    drive(app, script)
    assert all("1/2" in line for line in seen), seen


# ======================================================= tasks / schedules / doctor

def status_text(app) -> str:
    """FR-702's home: one row, docked, never hidden."""
    from textual.widgets import Static

    node = app.screen.query_one("#status", Static)
    return getattr(node.content, "plain", str(node.content))


class Opened(NoesisApp):
    """A workspace with one pane already split in.

    The pickers used to be pushed screens; they are panes over the same data
    now, so the way to open one is the way a person opens one.
    """

    def __init__(self, pane_id) -> None:
        # Through the constructor, not an on_mount override: textual dispatches
        # `on_mount` to EVERY class in the MRO, so overriding it would have run
        # the base's too and opened a second workspace on top.
        super().__init__(FakeGraph(), thread="picker", pane=pane_id)
        self.answer = "UNSET"

    def open_workspace(self, goal=None, thread=None, pane=None):
        if pane is None and thread not in (None, "picker"):
            self.answer = thread        # what selecting a row hands back
            return
        super().open_workspace(goal=goal, thread=thread, pane=pane)


def _cells(pilot):
    from textual.widgets import DataTable

    table = pilot.app.screen.query_one(DataTable)
    return [str(c) for row in table.get_row_at_all() for c in row] \
        if hasattr(table, "get_row_at_all") else \
        [str(table.get_cell_at((r, c)))
         for r in range(table.row_count) for c in range(len(table.columns))]


def test_the_task_screen_shows_what_the_queue_holds():
    worker.submit("count the files")

    app = Opened("tasks")
    seen = []

    async def script(pilot):
        await pilot.pause()
        await pilot.pause()
        seen.extend(_cells(pilot))

    drive(app, script)
    assert any("count the files" in c for c in seen)
    assert any("queued" == c for c in seen)


def test_selecting_a_task_returns_its_id_because_that_IS_the_thread_id():
    task_id = worker.submit("count the files")

    app = Opened("tasks")

    async def script(pilot):
        await pilot.pause()
        await pilot.pause()
        pilot.app.screen.query_one(DataTable).action_select_cursor()
        await pilot.pause()

    drive(app, script)
    assert app.answer == task_id


def test_the_task_screen_shows_the_answer_the_run_produced():
    """UR-16, and the other half of the worker fix: `detail` is what was said."""
    task_id = worker.submit("what python version")
    worker.conclude(task_id, status="done", verdict="done",
                    detail="Python 3.13.")

    app = Opened("tasks")
    shown = []

    async def script(pilot):
        from textual.widgets import Static

        await pilot.pause()
        await pilot.pause()
        node = pilot.app.screen.query_one("#task-detail", Static)
        shown.append(getattr(node.content, "plain", str(node.content)))

    drive(app, script)
    assert "Python 3.13." in shown[0]


def test_the_schedule_screen_lists_and_removes():
    sched_id = worker.schedule("0 9 * * 1", "weekly review")

    app = Opened("schedules")

    async def script(pilot):
        await pilot.pause()
        await pilot.pause()
        pilot.app.screen.query_one(SchedulesPane).action_remove()
        await pilot.pause()

    drive(app, script)
    assert worker.schedules() == [], f"{sched_id} should be gone"


def test_the_doctor_renders_every_line_it_is_given(monkeypatch):
    from agent import channel

    monkeypatch.setattr(channel, "diagnose",
                        lambda: ["ok    provider nvidia", "FAIL  workspace missing"])
    app = Opened("doctor")
    lines = []

    async def script(pilot):
        await pilot.pause()
        await pilot.pause()
        await pilot.app.screen.query_one(DoctorPane).workers.wait_for_complete()
        await pilot.pause()
        from textual.widgets import RichLog

        log = pilot.app.screen.query_one("#doctor-log", RichLog)
        lines.extend(str(s) for s in log.lines)

    drive(app, script)
    joined = " ".join(lines)
    assert "provider nvidia" in joined and "workspace missing" in joined


def test_a_doctor_that_RAISES_reports_it_rather_than_killing_the_screen(monkeypatch):
    """A probe that dials IMAP can raise anything; a traceback here says nothing."""
    from agent import channel

    def boom():
        raise OSError("network unreachable")

    monkeypatch.setattr(channel, "diagnose", boom)
    app = Opened("doctor")
    lines = []

    async def script(pilot):
        await pilot.pause()
        await pilot.pause()
        await pilot.app.screen.query_one(DoctorPane).workers.wait_for_complete()
        await pilot.pause()
        from textual.widgets import RichLog

        log = pilot.app.screen.query_one("#doctor-log", RichLog)
        lines.extend(str(s) for s in log.lines)

    drive(app, script)
    joined = " ".join(lines)
    assert "FAIL" in joined and "network unreachable" in joined


def test_a_failing_line_is_not_styled_like_a_passing_one():
    assert _doctor_text("FAIL  workspace missing").style == "row--denied"
    assert _doctor_text("ok    provider nvidia").style == "row--ran"
    assert _doctor_text("--    email channel not configured").style == "row--muted"


# ====================================================== slash commands

class EmptyCheckpointer:
    """What ThreadsScreen walks. Empty, because the command is what is on
    trial here, not the picker it opens."""

    def list(self, _config):
        return []


class Counted(FakeGraph):
    """A graph that says whether the input box ever reached it."""

    def __init__(self, values=None):
        super().__init__(values)
        self.calls = 0
        self.checkpointer = EmptyCheckpointer()

    def invoke(self, payload, cfg):
        self.calls += 1
        return super().invoke(payload, cfg)


IDLE = {"messages": [], "turns": 0, "max_turns": 30, "spent_tokens": 0,
        "verdict": "done", "plan": [], "cursor": 0}


def _type(pilot, text):
    from textual.widgets import Input

    box = pilot.app.screen.query_one(Input)
    box.value = text
    return pilot.press("enter")


def _run_command(text, graph=None):
    """Type `text`, submit it, and report where the app ended up."""
    graph = graph or Counted(dict(IDLE))
    app = NoesisApp(graph, goal=None, thread="abc12345")
    out = {}

    async def script(pilot):
        await _type(pilot, text)
        await pilot.pause()
        session = next(s for s in pilot.app.screen_stack
                       if isinstance(s, WorkspaceScreen))
        # A command opens a PANE now. Where it landed is which panes are up.
        out["screen"] = type(pilot.app.screen).__name__
        out["panes"] = session.showing()
        out["transcript"] = "\n".join(
            strip.text for strip in session.query_one("#chat-log").lines)

    drive(app, script)
    out["calls"] = graph.calls
    return out


@pytest.mark.parametrize("command,pane", [
    ("/tasks", "tasks"),
    ("/schedules", "schedules"),
    ("/doctor", "doctor"),
    ("/threads", "threads"),
])
def test_a_slash_command_opens_its_pane(command, pane):
    assert pane in _run_command(command)["panes"]


def test_a_command_never_reaches_the_model():
    """The point of intercepting in _submitted rather than in the graph: a
    command is navigation, and billing a turn for it would be absurd."""
    assert _run_command("/tasks")["calls"] == 0


def test_an_unknown_command_answers_with_the_list_rather_than_guessing():
    out = _run_command("/taks")

    assert out["calls"] == 0
    assert "no such command" in out["transcript"]
    assert "/tasks" in out["transcript"]


def test_a_sentence_that_merely_STARTS_with_a_slash_is_a_message():
    """"/usr/bin/python is missing" is a goal, not a mistyped command.
    Swallowing it would lose the message with no way to get it back."""
    out = _run_command("/usr/bin/python is missing")

    assert out["calls"] == 1
    assert out["panes"] == ["chat"], "a message must not open a pane"


def test_help_lists_every_command_that_exists():
    out = _run_command("/help")

    assert out["panes"] == ["chat"], "the list belongs in the scrollback"
    for name in COMMANDS:
        assert name in out["transcript"], name


def test_no_keystroke_navigates_to_another_screen():
    """Navigation between VIEWS is typed, not pressed. tab cycles pane focus
    now, and the function keys that used to open screens do nothing at all."""
    app = NoesisApp(Counted(dict(IDLE)), goal=None, thread="abc12345")
    landed = []

    async def script(pilot):
        for key in ("tab", "f2", "f3", "f4"):
            await pilot.press(key)
            await pilot.pause()
            landed.append(type(pilot.app.screen).__name__)

    drive(app, script)
    assert landed == ["WorkspaceScreen"] * 4


def test_the_input_completes_the_commands_it_accepts():
    """One list behind the suggester and the dispatcher, so a command that
    completes cannot be one that does not run."""
    from textual.widgets import Input

    app = NoesisApp(Counted(dict(IDLE)), goal=None, thread="abc12345")
    found = []

    async def script(pilot):
        box = pilot.app.screen.query_one(Input)
        found.append(await box.suggester.get_suggestion("/ta"))

    drive(app, script)
    assert found[0] == "/tasks"
