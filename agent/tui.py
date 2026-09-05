"""Textual front end (FR-701, FR-703).

    python -m agent --tui "goal"       start a session
    python -m agent --tui              open on the thread picker
    python -m agent --tui --resume ID  open one thread

The SECOND implementation of the interface layer, which is what earns it a
module under CE-01 - the same standard `provider.py` met with a second provider.
`cli.py` remains the headless, scriptable path and is untouched by this file.

What it adds over the CLI is not decoration. FR-701's word is "chat", and until
now a follow-up meant killing the process and running `--resume` with nothing
new to say. Here the input box stays live: when a thread terminates, the next
message re-enters it with its history intact (`graph.continue_state`).

THE THREADING MODEL, which decides everything else here. `app.invoke()` is
synchronous and Textual owns an event loop, so the graph runs in a thread
worker. Everything it reports - `trace.append`, `on_text` - arrives on that
thread and crosses back through `call_from_thread`. The approval pause needs
the same primitive in reverse: `gate` suspends, `invoke()` returns with
`__interrupt__`, and the worker must BLOCK until a human answers. Textual's
`call_from_thread` runs a coroutine and returns its result to the calling
thread, which is exactly that.

Driving the graph off the main thread is safe only because `get_app()` opens
SQLite with `check_same_thread=False`. That was already true; nothing here
changed it.
"""
from __future__ import annotations

import time
import uuid

from langgraph.types import Command
from rich.markdown import Markdown
from rich.text import Text
from rich.padding import Padding
from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen, Screen
from textual.suggester import SuggestFromList
from textual.widgets import (Button, DataTable, Footer, Header, Input, Label,
                             RichLog, Static)
from textual.worker import Worker, WorkerState

from agent import cli
from agent import config as settings
from agent import graph


# --------------------------------------------------------------- renderables

def _tool_text(e: dict) -> Text:
    """One coloured line per tool call, in `cli._tool_line`'s columns.

    A rich `Text` rather than console markup, and the log below has markup
    turned OFF, because `summary` carries arbitrary file content and shell
    arguments - a `[/]` in someone's code must render as a `[/]`, not be parsed
    as a closing tag.
    """
    if e.get("verdict") == "deny":
        style, mark = "yellow", "!"
    elif e.get("is_error"):
        style, mark = "red", "x"
    else:
        style, mark = "green", ">"
    line = Text("     ")
    line.append(f"{mark} ", style=style)
    line.append(f"{e['tool']:<10} ", style=style)
    line.append(f"{e.get('summary', '')[:44]:<44}")
    line.append(f"{e.get('duration_ms', 0) / 1000:>6.1f}s", style="dim")
    if e.get("verdict") == "deny":
        line.append("  DENIED", style="bold yellow")
    elif e.get("is_error"):
        line.append("  ERROR", style="bold red")
    if e.get("spill_path"):
        line.append("  [spilled]", style="dim cyan")
    return line


def _said(who: str, text: str):
    """One turn of the conversation, with a gutter that says whose it is.

    Everything used to arrive at the same indent - the model's prose, the tool
    lines, the verdict - so a transcript read as one undifferentiated wall. The
    gutter is four columns and costs nothing.
    """
    if who:
        head = Text(f"\n{who:<4} ", style="bold cyan")
        head.append(text.strip(), style="none")
        return head
    body = Markdown(text.strip())
    return Padding(body, (0, 0, 0, 5))


def _replay(messages: list[dict]) -> list:
    """The transcript of a thread already on disk, so resuming shows a
    conversation rather than an empty screen with history it will not admit to.

    Tool RESULTS are deliberately skipped: they are already summarised on the
    call's own line, and a resumed thread would otherwise open with several
    screens of shrunk file contents.
    """
    out = []
    for message in messages:
        content = message.get("content")
        if message.get("role") == "user":
            if isinstance(content, str):
                out.append(_said("you", content))
            continue
        for block in content if isinstance(content, list) else []:
            if block.get("type") == "text" and block.get("text", "").strip():
                out.append(_said("", block["text"]))
            elif block.get("type") == "tool_use":
                out.append(_tool_text({"tool": block.get("name", "?"),
                                       "summary": str(block.get("input", ""))[:46]}))
    return out


# ------------------------------------------------------------------ approval

# The slash commands, and the only list of them. `Input`'s suggester completes
# from these keys, `_command` dispatches on them, and /help prints them.
COMMANDS = {
    "/tasks": "the queue - what ran, what it answered, what is waiting",
    "/schedules": "cron schedules, soonest first",
    "/doctor": "every precondition, each line ok or FAIL",
    "/threads": "past threads, newest first",
    "/help": "this list",
}


def _help_text() -> Text:
    """The command list, rendered into the transcript rather than a modal.

    It belongs in the scrollback: a modal you have to dismiss to type the thing
    it just told you about is a worse way to learn five words.
    """
    out = Text()
    for name, what in COMMANDS.items():
        out.append(f"{name:<12}", style="bold")
        out.append(f"{what}\n", style="dim")
    return out


def _doctor_text(line: str) -> Text:
    """One diagnostic line, coloured by its verdict rather than its wording.

    `diagnose()` returns strings that already begin ok / FAIL / --, so the
    marker is the only thing read here; the text after it is free-form.
    """
    style = ("bold red" if line.startswith("FAIL")
             else "dim" if line.startswith("--") else "")
    return Text(line, style=style)


class ApprovalScreen(ModalScreen[str]):
    """The paused call, and one answer (FR-306, NFR-801).

    Every argument is shown in full, never abbreviated - the CLI's rule, and the
    reason is the same: a prompt that elides the dangerous half of a command
    manufactures consent instead of obtaining it.

    A modal has dismissal paths a keystroke loop cannot have, and each of them
    resolves to DENY: escape, and any dismissal that is not the allow button.
    Silence is not consent, exactly as the CLI's EOFError branch already says.
    """

    BINDINGS = [("escape", "refuse", "deny")]

    def __init__(self, payload: dict) -> None:
        super().__init__()
        self._payload = payload

    def compose(self) -> ComposeResult:
        call = self._payload["call"]
        with Vertical(id="approval"):
            yield Label(f"APPROVAL NEEDED   {call['name']}", id="approval-title")
            for key, value in call["input"].items():
                yield Static(Text(f"  {key}: {value}"), classes="arg")
            yield Label(f"Reason: {self._payload.get('reason', '')}", classes="why")
            with Horizontal(id="approval-buttons"):
                yield Button("Allow", id="allow", variant="error")
                yield Button("Deny", id="deny", variant="primary")
                yield Button("Quit", id="quit")

    def on_mount(self) -> None:
        # Deny takes focus, so a reflexive Enter refuses. The dangerous answer
        # must be the one that takes a deliberate keystroke.
        self.query_one("#deny", Button).focus()

    @on(Button.Pressed)
    def _pressed(self, event: Button.Pressed) -> None:
        self.dismiss({"allow": "allow", "deny": "deny",
                      "quit": cli.QUIT}[event.button.id])

    def action_refuse(self) -> None:
        self.dismiss("deny")


class PlanScreen(ModalScreen[str]):
    """The plan, before anything has been changed (UR-02, UR-05).

    Dismissal resolves to REVISE, never accept - the same rule as the tool
    modal's deny, and for the same reason: an approval nobody gave must not be
    manufactured by a stray escape. Revise is also the cheap direction, since
    nothing has been written yet.
    """

    BINDINGS = [("escape", "revise", "revise")]

    def __init__(self, payload: dict) -> None:
        super().__init__()
        self._payload = payload

    def compose(self) -> ComposeResult:
        with Vertical(id="plan"):
            yield Label("PLAN", id="plan-title")
            for number, step in enumerate(self._payload["plan"], 1):
                yield Static(Text(f"  {number}. {step}"), classes="step")
            with Horizontal(id="plan-buttons"):
                yield Button("Accept", id="accept", variant="primary")
                yield Button("Revise", id="revise")
                yield Button("Quit", id="quit")

    def on_mount(self) -> None:
        # Accept holds focus here, unlike the tool modal where deny does.
        # Nothing has been changed yet, so the reflexive key is the harmless one.
        self.query_one("#accept", Button).focus()

    @on(Button.Pressed)
    def _pressed(self, event: Button.Pressed) -> None:
        self.dismiss({"accept": "accept", "revise": "revise",
                      "quit": cli.QUIT}[event.button.id])

    def action_revise(self) -> None:
        self.dismiss("revise")


# ------------------------------------------------------------------- threads

class ThreadsScreen(Screen[str]):
    """Past threads, newest first; Enter resumes one (FR-703).

    Reads `cli._thread_rows`, so the picker and `--list` cannot drift apart
    about what a thread's goal or verdict was.
    """

    BINDINGS = [("escape", "app.pop_screen", "back")]

    def compose(self) -> ComposeResult:
        yield Header()
        yield DataTable(id="threads")
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one(DataTable)
        table.cursor_type = "row"
        table.add_columns("thread", "verdict", "turns", "goal")
        rows = cli._thread_rows(self.app.graph)
        for row in rows:
            table.add_row(row["id"], row["verdict"], str(row["turns"]),
                          row["goal"][:60], key=row["id"])
        if not rows:
            self.notify("no threads yet")

    @on(DataTable.RowSelected)
    def _pick(self, event: DataTable.RowSelected) -> None:
        self.dismiss(str(event.row_key.value))


# --------------------------------------------------------------------- tasks

class TasksScreen(Screen[str]):
    """The queue (FR-604); Enter opens a task's thread.

    A task id IS a thread id, so selecting a row hands back the same string
    SessionScreen already takes and no mapping exists to get wrong. Reads
    `worker.tasks()` - the function `--tasks` prints - so the screen and the
    flag cannot disagree about a status.
    """

    BINDINGS = [
        ("escape", "app.pop_screen", "back"),
        ("r", "refresh", "refresh"),
        ("c", "cancel", "cancel"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self._rows: dict[str, dict] = {}
        self._current: str | None = None

    def compose(self) -> ComposeResult:
        yield Header()
        yield DataTable(id="tasks")
        yield Static("", id="task-detail")
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one(DataTable)
        table.cursor_type = "row"
        table.add_columns("task", "status", "verdict", "goal")
        self.action_refresh()

    def action_refresh(self) -> None:
        from agent import worker

        table = self.query_one(DataTable)
        table.clear()
        self._rows = {row["id"]: row for row in worker.tasks()}
        for row in self._rows.values():
            table.add_row(row["id"], row["status"], row["verdict"] or "-",
                          row["goal"][:52], key=row["id"])
        if not self._rows:
            self.notify("no tasks yet")

    @on(DataTable.RowHighlighted)
    def _show(self, event: DataTable.RowHighlighted) -> None:
        """UR-16: what it answered, or refused, while nobody was watching."""
        self._current = str(event.row_key.value) if event.row_key else None
        row = self._rows.get(self._current or "")
        self.query_one("#task-detail", Static).update(
            Text((row or {}).get("detail") or "", style="dim"))

    @on(DataTable.RowSelected)
    def _pick(self, event: DataTable.RowSelected) -> None:
        self.dismiss(str(event.row_key.value))

    def action_cancel(self) -> None:
        from agent import worker

        if not self._current:
            return
        self.notify(f"cancelled {self._current}" if worker.cancel(self._current)
                    else f"{self._current} is not queued or running")
        self.action_refresh()


# ----------------------------------------------------------------- schedules

class SchedulesScreen(Screen):
    """Cron schedules, soonest first (FR-602)."""

    BINDINGS = [
        ("escape", "app.pop_screen", "back"),
        ("r", "refresh", "refresh"),
        ("d", "delete", "delete"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self._current: str | None = None

    def compose(self) -> ComposeResult:
        yield Header()
        yield DataTable(id="schedules")
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one(DataTable)
        table.cursor_type = "row"
        table.add_columns("schedule", "cron", "next run", "goal")
        self.action_refresh()

    def action_refresh(self) -> None:
        from agent import worker

        table = self.query_one(DataTable)
        table.clear()
        rows = worker.schedules()
        for row in rows:
            nxt = time.strftime(cli.TIME_FMT, time.localtime(row["next_run"]))
            table.add_row(row["id"], row["cron"], nxt, row["goal"][:44],
                          key=row["id"])
        if not rows:
            self.notify("no schedules")

    @on(DataTable.RowHighlighted)
    def _track(self, event: DataTable.RowHighlighted) -> None:
        self._current = str(event.row_key.value) if event.row_key else None

    def action_delete(self) -> None:
        from agent import worker

        if not self._current:
            return
        self.notify(f"removed {self._current}" if worker.unschedule(self._current)
                    else f"no such schedule: {self._current}")
        self.action_refresh()


# -------------------------------------------------------------------- doctor

class DoctorScreen(Screen):
    """Every precondition, each line ok or FAIL. Changes nothing.

    On a worker thread because `diagnose()` dials IMAP and SMTP: run inline it
    freezes the interface for two network round trips, and a doctor that looks
    hung is worse than no doctor.
    """

    BINDINGS = [
        ("escape", "app.pop_screen", "back"),
        ("r", "refresh", "re-run"),
    ]

    def compose(self) -> ComposeResult:
        yield Header()
        yield RichLog(id="doctor", wrap=True, markup=False)
        yield Footer()

    def on_mount(self) -> None:
        self.action_refresh()

    def action_refresh(self) -> None:
        log = self.query_one("#doctor", RichLog)
        log.clear()
        log.write(Text("probing...", style="dim"))
        self._probe()

    @work(thread=True, exclusive=True)
    def _probe(self) -> None:
        from agent import channel

        try:
            lines = channel.diagnose()
        except Exception as exc:                     # noqa: BLE001
            # A doctor that raises tells you nothing about what it was checking.
            lines = [f"FAIL  doctor: {type(exc).__name__}: {exc}"]
        self.app.call_from_thread(self._paint, lines)

    def _paint(self, lines: list[str]) -> None:
        log = self.query_one("#doctor", RichLog)
        log.clear()
        for line in lines:
            log.write(_doctor_text(line))


# ------------------------------------------------------------------- session

class SessionScreen(Screen):
    """One thread: the transcript, the live model text, and the input box."""

    BINDINGS = [
        # ctrl+c is what people actually press, and it used to escape Textual as
        # a KeyboardInterrupt that surfaced as a traceback out of mcp.shutdown().
        # Bound here it is an ordinary quit; the thread is already checkpointed.
        Binding("ctrl+c", "app.quit", "quit", show=False),
    ]

    def __init__(self, thread: str, goal: str | None = None) -> None:
        super().__init__()
        self.thread = thread
        self._goal = goal
        self._queued: list[str] = []
        self._stream = ""
        self._recalled = False
        self._step = None

    # ---------------------------------------------------------------- layout

    def compose(self) -> ComposeResult:
        yield Header()
        yield RichLog(id="log", wrap=True, markup=False, auto_scroll=True)
        yield Static("", id="status")
        yield Input(placeholder="say something, or /help", id="say",
                    suggester=SuggestFromList(COMMANDS, case_sensitive=False))
        yield Footer()

    def on_mount(self) -> None:
        self._retitle()
        prior = self._values()
        for renderable in _replay(prior.get("messages") or []):
            self._write(renderable)
        if self._goal:
            # The goal belongs in the transcript. Without it the first thing you
            # see after typing a goal is an empty screen, which reads as broken.
            self._write(_said("you", self._goal))
            self._begin(self._goal)
        elif prior and prior.get("verdict") is None and prior.get("messages"):
            # Interrupted mid-run: continue the checkpoint rather than waiting
            # for a message that would only be appended to a half-finished turn.
            self._start(None)
        self.query_one(Input).focus()

    # ------------------------------------------------------------- the graph

    def _cfg(self) -> dict:
        return {"configurable": {"thread_id": self.thread}}

    def _values(self) -> dict:
        return self.app.graph.get_state(self._cfg()).values or {}

    def _begin(self, text: str) -> None:
        """A message from the human: seed a fresh thread or continue one."""
        prior = self._values()
        self._start(graph.continue_state(prior, text) if prior.get("messages")
                    else graph.new_state(text))

    @work(thread=True, exclusive=True)
    def _start(self, payload: dict | None) -> None:
        """The graph, on a worker thread. Everything it says crosses back to the
        event loop through call_from_thread; nothing here touches a widget."""
        app = self.app
        app.call_from_thread(self._status, Text("working...", style="dim italic"))
        trace = cli.LiveTrace(sink=lambda e: app.call_from_thread(self._on_trace, e))
        cfg = {"configurable": {
            "thread_id": self.thread,
            "autonomous": False,   # the switch that makes `confirm` pause, not refuse
            "trace": trace,
            "on_text": lambda t: app.call_from_thread(self._on_text, t),
        }}
        try:
            out = self.app.graph.invoke(payload, cfg)
            while "__interrupt__" in out:
                paused = out["__interrupt__"][0].value
                # Two things can pause a run now: a tool call the gate wants
                # approved, and a plan waiting to be adopted. They are told
                # apart by the payload, the same way cli.ask_human does it.
                planning = "plan" in paused
                answer = app.call_from_thread(
                    app.push_screen_wait,
                    PlanScreen(paused) if planning else ApprovalScreen(paused))
                if answer == cli.QUIT:
                    app.call_from_thread(
                        self._note,
                        "stopped, still checkpointed - reopen it with tab", "yellow")
                    return
                if planning:
                    # Anything other than an explicit accept revises, so a
                    # dismissed modal cannot adopt a plan nobody agreed to.
                    resume = "accept" if answer == "accept" else "revise"
                else:
                    # Anything that is not an explicit allow is a refusal. The
                    # gate reads it the same way; normalising here keeps a
                    # dismissed modal (None) from reaching it as something else.
                    resume = "allow" if answer == "allow" else "deny"
                out = self.app.graph.invoke(Command(resume=resume), cfg)
        except Exception as exc:                       # noqa: BLE001 - see below
            # A provider error, a rate limit, a dead MCP server. Textual would
            # otherwise log it to a devtools console nobody is watching and the
            # screen would just stop moving.
            app.call_from_thread(self._note, f"{type(exc).__name__}: {exc}", "red")

    # -------------------------------------------------------------- reactions

    def _on_trace(self, e: dict) -> None:
        kind = e.get("kind")
        if kind == "model":
            self._flush()
            self._retitle()
        elif kind == "tool":
            self._write(_tool_text(e))
        elif kind == "memory" and not self._recalled:
            # Once per session, not once per turn: `act` re-injects it every
            # turn and a line each time would say nothing new.
            self._recalled = True
            self._note(f"recalled {e['chars']} chars of memory", "dim")
        elif kind == "step":
            # FR-702's other half. Announced only when it CHANGES: act emits one
            # of these every working turn, and repeating the same line each turn
            # would bury the transcript.
            if e["cursor"] != self._step:
                self._step = e["cursor"]
                self._note(f"step {e['cursor'] + 1}/{e['of']}: {e['text']}",
                           "bold blue")
            self._retitle()
        elif kind == "skill":
            self._note(f"learned skill: {e['name']}", "dim cyan")
        elif kind == "terminal":
            self._flush()
            colour = {"done": "bold green", "stuck": "bold yellow"}.get(
                e["verdict"], "bold red")
            self._note(f"\n{e['verdict']} - {e['turns']} turns, "
                       f"{e['spent_tokens']:,} tokens", colour)
            self._retitle()

    def _on_text(self, text: str) -> None:
        """Model prose as it arrives.

        Buffered rather than written straight to the log because the two
        providers deliver it differently - Anthropic streams deltas, the
        OpenAI-compatible path hands over one finished block - and a log line
        per delta would shred a sentence into one word per row. The buffer
        renders live on the status line and moves into the log as markdown at
        the end of the turn, so it is genuinely streamed AND readable after.
        """
        self._stream += text
        self._status(Text(self._stream.strip()[-400:], style="italic"))

    def _flush(self) -> None:
        if self._stream.strip():
            self._write(_said("", self._stream))
        self._stream = ""
        self._status("")

    def _status(self, renderable) -> None:
        """The one line between the transcript and the input.

        Carries the streaming reply, or "working" while a turn is in flight.
        Collapsed to nothing when idle - an always-present blank line is what
        made the first version look like it had a hole in it. It exists because
        a model turn takes tens of seconds, and a screen that shows nothing at
        all for that long reads as a hung program rather than a busy one.
        """
        widget = self.query_one("#status", Static)
        widget.update(renderable)
        widget.display = bool(renderable if isinstance(renderable, str)
                              else str(renderable).strip())

    # ----------------------------------------------------------------- input

    @on(Input.Submitted, "#say")
    def _submitted(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        if not text:
            return
        event.input.value = ""
        if self._command(text):
            return
        self._write(_said("you", text))
        if self._busy():
            # Typed mid-run. Queued rather than dropped or interleaved: the
            # message list belongs to the graph while a turn is in flight.
            self._queued.append(text)
            self._note("queued until this turn finishes", "dim")
            return
        self._begin(text)

    @on(Worker.StateChanged)
    def _worker_done(self, event: Worker.StateChanged) -> None:
        if event.state in (WorkerState.SUCCESS, WorkerState.ERROR,
                           WorkerState.CANCELLED):
            self._flush()
            self._retitle()
        if event.state is WorkerState.SUCCESS and self._queued:
            self._begin(self._queued.pop(0))

    def _busy(self) -> bool:
        return any(w.is_running for w in self.workers)

    def _command(self, text: str) -> bool:
        """Run a slash command. False means it was an ordinary message.

        A command is a lone slash word: "/usr/bin/python is missing" is a
        sentence and must reach the model, while "/taks" is a typo and gets the
        list rather than being silently sent as a goal. The further slash is
        what separates them.
        """
        if not text.startswith("/"):
            return False
        word = text.split()[0].lower()
        if "/" in word[1:]:
            return False                  # a path, and paths are not commands
        if word not in COMMANDS:
            self._write(_help_text())
            self._note(f"no such command: {word}")
            return True
        if word == "/help":
            self._write(_help_text())
        else:
            getattr(self, f"action_{word[1:]}")()
        return True

    def action_threads(self) -> None:
        self.app.push_screen(ThreadsScreen(), callback=self._switch)

    def action_tasks(self) -> None:
        self.app.push_screen(TasksScreen(), callback=self._switch)

    def action_schedules(self) -> None:
        self.app.push_screen(SchedulesScreen())

    def action_doctor(self) -> None:
        self.app.push_screen(DoctorScreen())

    def _switch(self, thread: str | None) -> None:
        if thread and thread != self.thread:
            self.app.switch_screen(SessionScreen(thread))

    # ---------------------------------------------------------------- output

    def _write(self, renderable) -> None:
        self.query_one("#log", RichLog).write(renderable)

    def _note(self, text: str, style: str = "dim") -> None:
        self._write(Text(text, style=style))

    def _retitle(self) -> None:
        """FR-702: the plan and the active step, visible at all times.

        The step sits first because it is the one thing that says WHERE in the
        work the run is; turns and tokens say only how much is left.
        """
        values = self._values()
        plan = values.get("plan") or []
        step = (f"step {min(values.get('cursor', 0), len(plan) - 1) + 1}/{len(plan)}   "
                if plan else "")
        if values.get("phase") == "planning":
            step = "planning   "
        self.app.sub_title = (
            f"{self.thread}   {step}"
            f"turn {values.get('turns', 0)}/"
            f"{values.get('max_turns', settings.MAX_TURNS)}"
            f"   {values.get('spent_tokens', 0):,} tokens"
            f"   {values.get('verdict') or 'running'}")


# ----------------------------------------------------------------------- app

class AgentTUI(App):
    """One session screen, four pickers over it, two modals.

    The pickers are READ paths over state the CLI already prints, plus the two
    mutations it already offers (cancel, unschedule). Anything that would call
    a model belongs in SessionScreen, which owns the thread worker.
    """

    TITLE = "personal-agent"
    BINDINGS = [Binding("ctrl+q", "quit", "quit", priority=True)]
    CSS = """
    /* No frame around the transcript. A bordered box with four lines in it and
       twenty blank ones below reads as a mostly-empty container rather than a
       conversation that has just started. */
    #log { height: 1fr; padding: 0 1; background: $surface; }
    #status { padding: 0 2; color: $text-muted; height: auto; }
    #tasks, #schedules { height: 1fr; }
    #task-detail { height: auto; padding: 0 2; }
    #doctor { height: 1fr; padding: 0 1; }
    #say { dock: bottom; border: none; border-top: tall $panel; padding: 0 1; }
    #say:focus { border-top: tall $accent; }
    ApprovalScreen { align: center middle; }
    #approval {
        width: 78; height: auto; padding: 1 2;
        background: $surface; border: thick $error;
    }
    #approval-title { text-style: bold; color: $error; margin-bottom: 1; }
    .arg { margin-left: 1; }
    .why { margin-top: 1; color: $text-muted; }
    #approval-buttons { height: auto; margin-top: 1; align-horizontal: center; }
    #approval-buttons Button { margin: 0 1; }
    PlanScreen { align: center middle; }
    #plan {
        width: 78; height: auto; padding: 1 2;
        background: $surface; border: thick $primary;
    }
    #plan-title { text-style: bold; color: $primary; margin-bottom: 1; }
    .step { margin-left: 1; }
    #plan-buttons { height: auto; margin-top: 1; align-horizontal: center; }
    #plan-buttons Button { margin: 0 1; }
    """

    def __init__(self, graph_app, goal: str | None, thread: str | None) -> None:
        super().__init__()
        self.graph = graph_app
        self._goal = goal
        self._thread = thread or uuid.uuid4().hex[:8]

    def on_mount(self) -> None:
        self.push_screen(SessionScreen(self._thread, self._goal))


def run(graph_app, goal: str | None = None, thread: str | None = None) -> int:
    """Start the TUI. Ctrl+C is an exit, not a crash.

    Measured, not anticipated: interrupting a live session printed a traceback
    out of `mcp.shutdown()` joining its transport thread. The work is already
    checkpointed at that point, so the interrupt has cost nothing and must not
    look like it has.
    """
    try:
        AgentTUI(graph_app, goal, thread).run()
    except KeyboardInterrupt:
        pass
    return 0
