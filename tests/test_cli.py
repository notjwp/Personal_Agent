"""CLI: the live display and the approval prompt.

Worth testing rather than eyeballing, because this is where consent is decided.
A prompt that reads a mistyped key as approval is a security defect, and the
only way that surfaces in manual testing is by accident.
"""
import builtins

import pytest

from agent import cli


def answers(monkeypatch, *replies):
    """Feed scripted keystrokes to input()."""
    queue = list(replies)
    monkeypatch.setattr(builtins, "input", lambda _="": queue.pop(0))
    return queue


PAUSE = {"call": {"id": "t1", "name": "run_shell",
                  "input": {"command": "rm -rf build", "timeout": 120}},
         "reason": "run_shell classified destructive"}


# ==================================================================== approval

@pytest.mark.parametrize("keystroke", ["a", "allow", "A", "  a  "])
def test_allow_variants_approve(monkeypatch, keystroke):
    answers(monkeypatch, keystroke)
    assert cli.ask_human(PAUSE) == "allow"


@pytest.mark.parametrize("keystroke", ["d", "deny", "D"])
def test_deny_variants_reject(monkeypatch, keystroke):
    answers(monkeypatch, keystroke)
    assert cli.ask_human(PAUSE) == "deny"


def test_quit_is_distinct_from_deny(monkeypatch):
    """Quitting stops the session; denying answers the question and continues.
    Collapsing them would silently turn a walk-away into a policy decision."""
    answers(monkeypatch, "q")
    assert cli.ask_human(PAUSE) == cli.QUIT
    assert cli.QUIT != "deny" and cli.QUIT != "allow"


def test_unrecognised_input_reprompts_rather_than_guessing(monkeypatch):
    """NFR-801: one keystroke resolves it - but only a RECOGNISED one. A typo
    must never be read as consent."""
    remaining = answers(monkeypatch, "yes", "maybe", "", "a")
    assert cli.ask_human(PAUSE) == "allow"
    assert remaining == [], "every answer should have been consumed"


def test_no_terminal_means_deny(monkeypatch):
    """Silence is not consent. Piped or detached, the answer is no."""
    def raise_eof(_=""):
        raise EOFError
    monkeypatch.setattr(builtins, "input", raise_eof)
    assert cli.ask_human(PAUSE) == "deny"


def test_prompt_shows_every_argument_in_full(monkeypatch, capsys):
    """FR-306: unabbreviated. A prompt that elides the dangerous half of a
    command manufactures consent instead of obtaining it."""
    answers(monkeypatch, "d")
    cli.ask_human({"call": {"id": "t1", "name": "run_shell",
                            "input": {"command": "rm -rf " + "deep/" * 40}},
                   "reason": "run_shell classified destructive"})
    shown = capsys.readouterr().out
    assert "rm -rf " + "deep/" * 40 in shown
    assert "..." not in shown


# ================================================================ live display

def test_live_trace_still_behaves_as_a_list():
    """The harness passes a plain list and must stay unaffected by this
    subclass existing."""
    trace = cli.LiveTrace()
    trace.append({"kind": "tool", "tool": "read_file", "summary": "a.py",
                  "duration_ms": 12, "is_error": False, "verdict": "auto"})
    assert len(trace) == 1 and trace[0]["tool"] == "read_file"


def test_live_trace_renders_each_event_as_it_arrives(capsys):
    trace = cli.LiveTrace()
    trace.append({"kind": "model", "billed_tokens": 1500})
    trace.append({"kind": "tool", "tool": "run_shell", "summary": "pytest -q",
                  "duration_ms": 2400, "is_error": True, "verdict": "auto",
                  "spill_path": ""})
    trace.append({"kind": "terminal", "verdict": "done", "turns": 3,
                  "spent_tokens": 5320})
    out = capsys.readouterr().out

    assert "turn 1/" in out and "1,500 tokens" in out
    assert "run_shell" in out and "pytest -q" in out and "2.4s" in out
    assert "ERROR" in out
    assert "done" in out and "5,320 tokens" in out


def test_denied_and_spilled_calls_are_visibly_marked(capsys):
    trace = cli.LiveTrace()
    trace.append({"kind": "tool", "tool": "write_file", "summary": "../escape.txt",
                  "duration_ms": 0, "is_error": True, "verdict": "deny",
                  "spill_path": ""})
    trace.append({"kind": "tool", "tool": "run_shell", "summary": "ls -R /",
                  "duration_ms": 30, "is_error": False, "verdict": "auto",
                  "spill_path": "/workspace/.agent/artifacts/abc.txt"})
    out = capsys.readouterr().out
    assert "DENIED" in out
    assert "[spilled]" in out


def test_token_counter_accumulates_across_turns(capsys):
    trace = cli.LiveTrace()
    trace.append({"kind": "model", "billed_tokens": 1000})
    trace.append({"kind": "model", "billed_tokens": 500})
    assert "turn 2/" in capsys.readouterr().out and trace.tokens == 1500


def test_resumed_session_continues_the_turn_count(monkeypatch, capsys):
    """A resumed run is one task continuing, not a new one. Restarting the
    counter at 1 while the run is really on turn 9 misreports how close it is
    to the turn cap - the one number the display exists to convey."""
    class FakeApp:
        checkpointer = None
        def get_state(self, _cfg):
            from types import SimpleNamespace
            return SimpleNamespace(values={"turns": 8, "spent_tokens": 42_000,
                                           "messages": [{"role": "user",
                                                         "content": "fix it"}]})
        def invoke(self, payload, cfg):
            assert payload is None, "resume must not seed a fresh state"
            cfg["configurable"]["trace"].append({"kind": "model", "billed_tokens": 100})
            return {"verdict": "done"}

    cli.run_session(None, "t-resume", FakeApp())
    out = capsys.readouterr().out
    assert "resumed at turn 8" in out and "42,000 tokens" in out
    assert "turn 9/" in out, "the next turn must be 9, not 1"
