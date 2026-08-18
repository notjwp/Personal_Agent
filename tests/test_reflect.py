"""reflect() — every verdict branch, in the order the checks must run.

The order is part of the contract, not an implementation detail, so precedence is
asserted directly rather than inferred.
"""
import pytest

from agent.graph import reflect


def state(**over):
    base = {
        "messages": [{"role": "user", "content": "fix it"}],
        "turns": 0, "max_turns": 12,
        "spent_tokens": 0, "budget_tokens": 200_000,
        "failures": 0, "verdict": None, "approved": [], "denied": [],
    }
    base.update(over)
    return base


def assistant_text(text="Looking into it."):
    return {"role": "assistant", "content": [{"type": "text", "text": text}]}


def assistant_call(name="run_shell", **args):
    return {"role": "assistant", "content": [
        {"type": "tool_use", "id": "t1", "name": name, "input": args or {"command": "ls"}}]}


def tool_result(is_error=False):
    return {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "t1", "content": "ok", "is_error": is_error}]}


# --- each branch in isolation ---------------------------------------------

def test_a_budget_over_threshold_compacts():
    assert reflect(state(spent_tokens=120_001))["verdict"] == "compact"


def test_a_budget_at_threshold_does_not_fire():
    """Strictly greater than 60%, not >=."""
    assert reflect(state(spent_tokens=120_000))["verdict"] != "compact"


def test_b_turn_cap_is_stuck():
    assert reflect(state(turns=12))["verdict"] == "stuck"


def test_c_three_identical_signatures_is_stuck():
    s = state(messages=[assistant_call(), tool_result()] * 3)
    assert reflect(s)["verdict"] == "stuck"


def test_c_two_identical_then_different_is_not_stuck():
    s = state(messages=[
        assistant_call(command="ls"), tool_result(),
        assistant_call(command="ls"), tool_result(),
        assistant_call(command="pytest"), tool_result(),
    ])
    assert reflect(s)["verdict"] != "stuck"


def test_d_three_consecutive_failures_replans():
    s = state(failures=3, messages=[assistant_call(), tool_result(is_error=True)])
    assert reflect(s)["verdict"] == "replan"


def test_f_after_a_tool_result_continues():
    assert reflect(state(messages=[assistant_call(), tool_result()]))["verdict"] == "continue"


# --- correction (b): the termination guard --------------------------------

def test_text_only_reply_before_any_tool_call_continues():
    """The exact trap: 'Let me look at the test file first.' must NOT finish the run.

    A cursor-based check with no plan node evaluates 1 >= 0 and returns `done` here.
    """
    s = state(messages=[
        {"role": "user", "content": "fix it"},
        assistant_text("Let me look at the test file first."),
    ])
    assert reflect(s)["verdict"] == "continue"


def test_text_only_reply_after_a_tool_call_is_done():
    s = state(messages=[
        {"role": "user", "content": "fix it"},
        assistant_call(), tool_result(),
        assistant_text("Tests pass."),
    ])
    assert reflect(s)["verdict"] == "done"


# --- precedence: the order of the checks is the contract -------------------

def test_budget_beats_turn_cap():
    assert reflect(state(spent_tokens=120_001, turns=99))["verdict"] == "compact"


def test_turn_cap_beats_thrash():
    s = state(turns=12, messages=[assistant_call(), tool_result()] * 3)
    assert reflect(s)["verdict"] == "stuck"   # (b) reached before (c); both say stuck


def test_thrash_beats_failures():
    s = state(failures=99, messages=[assistant_call(), tool_result(is_error=True)] * 3)
    assert reflect(s)["verdict"] == "stuck", "(c) must be evaluated before (d)"


def test_failures_beats_done():
    """A run that has failed three times in a row replans rather than declaring done."""
    s = state(failures=3, messages=[assistant_call(), tool_result(), assistant_text("Done.")])
    assert reflect(s)["verdict"] == "replan"


@pytest.mark.parametrize("verdict", ["done", "stuck", "compact", "replan", "continue"])
def test_every_verdict_is_reachable(verdict):
    """Guards against a branch being deleted or shadowed by a reordering."""
    cases = {
        "compact": state(spent_tokens=120_001),
        "stuck": state(turns=12),
        "replan": state(failures=3, messages=[assistant_call(), tool_result(is_error=True)]),
        "done": state(messages=[assistant_call(), tool_result(), assistant_text("Done.")]),
        "continue": state(messages=[assistant_call(), tool_result()]),
    }
    assert reflect(cases[verdict])["verdict"] == verdict
