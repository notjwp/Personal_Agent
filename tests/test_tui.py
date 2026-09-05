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

from agent import cli
from agent import worker
from agent.tui import (COMMANDS, AgentTUI, ApprovalScreen, DoctorScreen,
                       PlanScreen, SchedulesScreen, SessionScreen,
                       TasksScreen,
                       _doctor_text, _tool_text)


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
    log = app.screen.query_one("#log")
    return "\n".join(strip.text for strip in log.lines)


def test_a_trace_entry_from_the_worker_thread_reaches_the_transcript():
    """The mechanism the whole screen rests on: the graph runs on a thread and
    every line it produces crosses back through call_from_thread. If this hop is
    broken the UI simply stops moving, with no error anywhere."""
    app = AgentTUI(FakeGraph(), goal="fix the tests", thread="t1")
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
    app = AgentTUI(FakeGraph(), goal="fix the tests", thread="t1")
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

    app = AgentTUI(Broken(), goal="fix the tests", thread="t1")
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
    assert ok.plain.strip().startswith(">")
    assert "ERROR" in bad.plain and bad.plain.strip().startswith("x")
    assert "DENIED" in denied.plain and denied.plain.strip().startswith("!")


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
    app = AgentTUI(graph, goal=None, thread="9f2a1c")
    titles = []

    async def script(pilot):
        await pilot.pause()
        titles.append(pilot.app.sub_title)

    drive(app, script)
    assert "step 2/3" in titles[0]
    assert "turn 4/12" in titles[0] and "18,402 tokens" in titles[0]


def test_the_header_says_planning_before_a_plan_exists():
    graph = FakeGraph({"plan": [], "cursor": 0, "phase": "planning",
                       "turns": 0, "max_turns": 12, "spent_tokens": 0})
    app = AgentTUI(graph, goal=None, thread="9f2a1c")
    titles = []

    async def script(pilot):
        await pilot.pause()
        titles.append(pilot.app.sub_title)

    drive(app, script)
    assert "planning" in titles[0]


# ======================================================= tasks / schedules / doctor

class Opened(App):
    """Pushes one picker and keeps whatever it dismisses with."""

    def __init__(self, screen) -> None:
        super().__init__()
        self._screen = screen
        self.answer = "UNSET"

    def on_mount(self) -> None:
        self.push_screen(self._screen,
                         callback=lambda result: setattr(self, "answer", result))


def _cells(pilot):
    from textual.widgets import DataTable

    table = pilot.app.screen.query_one(DataTable)
    return [str(c) for row in table.get_row_at_all() for c in row] \
        if hasattr(table, "get_row_at_all") else \
        [str(table.get_cell_at((r, c)))
         for r in range(table.row_count) for c in range(len(table.columns))]


def test_the_task_screen_shows_what_the_queue_holds():
    worker.submit("count the files")

    app = Opened(TasksScreen())
    seen = []

    async def script(pilot):
        seen.extend(_cells(pilot))
        await pilot.press("escape")

    drive(app, script)
    assert any("count the files" in c for c in seen)
    assert any("queued" == c for c in seen)


def test_selecting_a_task_returns_its_id_because_that_IS_the_thread_id():
    task_id = worker.submit("count the files")

    app = Opened(TasksScreen())

    async def script(pilot):
        await pilot.press("enter")

    drive(app, script)
    assert app.answer == task_id


def test_the_task_screen_shows_the_answer_the_run_produced():
    """UR-16, and the other half of the worker fix: `detail` is what was said."""
    task_id = worker.submit("what python version")
    worker.conclude(task_id, status="done", verdict="done",
                    detail="Python 3.13.")

    app = Opened(TasksScreen())
    shown = []

    async def script(pilot):
        from textual.widgets import Static

        node = pilot.app.screen.query_one("#task-detail", Static)
        shown.append(getattr(node.content, "plain", str(node.content)))
        await pilot.press("escape")

    drive(app, script)
    assert "Python 3.13." in shown[0]


def test_the_schedule_screen_lists_and_removes():
    sched_id = worker.schedule("0 9 * * 1", "weekly review")

    app = Opened(SchedulesScreen())

    async def script(pilot):
        await pilot.press("d")
        await pilot.pause()

    drive(app, script)
    assert worker.schedules() == [], f"{sched_id} should be gone"


def test_the_doctor_renders_every_line_it_is_given(monkeypatch):
    from agent import channel

    monkeypatch.setattr(channel, "diagnose",
                        lambda: ["ok    provider nvidia", "FAIL  workspace missing"])
    app = Opened(DoctorScreen())
    lines = []

    async def script(pilot):
        await pilot.app.screen.workers.wait_for_complete()
        await pilot.pause()
        from textual.widgets import RichLog

        log = pilot.app.screen.query_one("#doctor", RichLog)
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
    app = Opened(DoctorScreen())
    lines = []

    async def script(pilot):
        await pilot.app.screen.workers.wait_for_complete()
        await pilot.pause()
        from textual.widgets import RichLog

        log = pilot.app.screen.query_one("#doctor", RichLog)
        lines.extend(str(s) for s in log.lines)

    drive(app, script)
    joined = " ".join(lines)
    assert "FAIL" in joined and "network unreachable" in joined


def test_a_failing_line_is_not_styled_like_a_passing_one():
    assert _doctor_text("FAIL  workspace missing").style == "bold red"
    assert _doctor_text("ok    provider nvidia").style == ""
    assert _doctor_text("--    email channel not configured").style == "dim"


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
    app = AgentTUI(graph, goal=None, thread="abc12345")
    out = {}

    async def script(pilot):
        await _type(pilot, text)
        await pilot.pause()
        out["screen"] = type(pilot.app.screen).__name__
        # The session screen may no longer be on top, and its log is where a
        # command writes; find it in the stack rather than on `app.screen`.
        session = next(s for s in pilot.app.screen_stack
                       if isinstance(s, SessionScreen))
        out["transcript"] = "\n".join(
            strip.text for strip in session.query_one("#log").lines)

    drive(app, script)
    out["calls"] = graph.calls
    return out


@pytest.mark.parametrize("command,screen", [
    ("/tasks", "TasksScreen"),
    ("/schedules", "SchedulesScreen"),
    ("/doctor", "DoctorScreen"),
    ("/threads", "ThreadsScreen"),
])
def test_a_slash_command_opens_its_screen(command, screen):
    assert _run_command(command)["screen"] == screen


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
    assert out["screen"] == "SessionScreen"


def test_help_lists_every_command_that_exists():
    out = _run_command("/help")

    assert out["screen"] == "SessionScreen"
    for name in COMMANDS:
        assert name in out["transcript"], name


def test_no_keystroke_navigates_anywhere():
    """Navigation is typed, not pressed. tab was the last key that moved
    screens; unbound, it goes back to being ordinary focus movement."""
    app = AgentTUI(Counted(dict(IDLE)), goal=None, thread="abc12345")
    landed = []

    async def script(pilot):
        for key in ("tab", "f2", "f3", "f4"):
            await pilot.press(key)
            await pilot.pause()
            landed.append(type(pilot.app.screen).__name__)

    drive(app, script)
    assert landed == ["SessionScreen"] * 4


def test_the_input_completes_the_commands_it_accepts():
    """One list behind the suggester and the dispatcher, so a command that
    completes cannot be one that does not run."""
    from textual.widgets import Input

    app = AgentTUI(Counted(dict(IDLE)), goal=None, thread="abc12345")
    found = []

    async def script(pilot):
        box = pilot.app.screen.query_one(Input)
        found.append(await box.suggester.get_suggestion("/ta"))

    drive(app, script)
    assert found[0] == "/tasks"
