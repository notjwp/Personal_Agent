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
from agent.tui import AgentTUI, ApprovalScreen, PlanScreen, _tool_text


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
