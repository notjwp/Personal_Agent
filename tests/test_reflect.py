"""reflect() — every verdict branch, in the order the checks must run.

The order is part of the contract, not an implementation detail, so precedence is
asserted directly rather than inferred.
"""
import pytest

from agent import config
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

def test_a_large_context_compacts():
    """FR-403 fires on CONTEXT SIZE, not on cumulative spend.

    The distinction is the whole reason compaction can exist: spent_tokens only
    ever grows, so a spend-based trigger would fire on every turn forever once
    crossed - compact, act, compact, act - now that the verdict is no longer
    terminal.
    """
    big = state(messages=[assistant_call(), tool_result()] * 400)
    assert reflect(big)["verdict"] == "compact"


def test_a_small_context_does_not_compact():
    assert reflect(state())["verdict"] != "compact"


def test_a0_budget_exhaustion_is_its_own_terminal_verdict():
    """NFR-401 asks for a HARD stop and nothing provided one: the old check fired
    at 60% and terminated, which looked like a budget stop while actually being a
    compaction trigger. FR-104 names "budget exhausted" as a terminal outcome."""
    assert reflect(state(spent_tokens=200_000))["verdict"] == "budget"
    assert reflect(state(spent_tokens=199_999))["verdict"] != "budget"


def test_a1_the_compaction_cap_stops_a_loop():
    """Compacted the maximum number of times and still over the threshold. Stop,
    rather than spend a model call per turn clearing nothing."""
    big = state(messages=[assistant_call(), tool_result()] * 400,
                compact_count=config.MAX_COMPACTIONS)
    assert reflect(big)["verdict"] == "stuck"


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


def test_d_three_consecutive_failures_is_stuck():
    """`replan` was never one of FR-104's four terminal outcomes, and it fired
    ONCE in 712 recorded rows. Three consecutive failures is a way of being
    stuck; `failures` stays on the row so the cause is still distinguishable."""
    s = state(failures=3, messages=[assistant_call(), tool_result(is_error=True)])
    assert reflect(s)["verdict"] == "stuck"


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

def test_budget_exhaustion_beats_everything():
    """A run that has spent its budget stops, whatever else is true - including a
    context large enough to compact, which would otherwise spend more."""
    big = state(messages=[assistant_call(), tool_result()] * 400,
                spent_tokens=200_000, turns=99)
    assert reflect(big)["verdict"] == "budget"


def test_compaction_beats_the_turn_cap():
    """Order matters: a run with room to compact should compact rather than be
    declared stuck, or compaction never fires on the runs that need it."""
    big = state(messages=[assistant_call(), tool_result()] * 400, turns=99)
    assert reflect(big)["verdict"] == "compact"


def test_turn_cap_beats_thrash():
    s = state(turns=12, messages=[assistant_call(), tool_result()] * 3)
    assert reflect(s)["verdict"] == "stuck"   # (b) reached before (c); both say stuck


def test_thrash_beats_failures():
    s = state(failures=99, messages=[assistant_call(), tool_result(is_error=True)] * 3)
    assert reflect(s)["verdict"] == "stuck", "(c) must be evaluated before (d)"


def test_failures_beats_done():
    """A run that has failed three times in a row is stuck rather than done."""
    s = state(failures=3, messages=[assistant_call(), tool_result(), assistant_text("Done.")])
    assert reflect(s)["verdict"] == "stuck"


@pytest.mark.parametrize("verdict", ["done", "stuck", "budget", "compact", "continue"])
def test_every_verdict_is_reachable(verdict):
    """Guards against a branch being deleted or shadowed by a reordering.

    FR-104: the TERMINAL outcomes are done, stuck and budget - the turn cap is
    reported as stuck. `compact` and `continue` are not terminal; compact now
    routes to the compaction node and back to act.
    """
    cases = {
        "compact": state(messages=[assistant_call(), tool_result()] * 400),
        "budget": state(spent_tokens=200_000),
        "stuck": state(turns=12),
        "done": state(messages=[assistant_call(), tool_result(), assistant_text("Done.")]),
        "continue": state(messages=[assistant_call(), tool_result()]),
    }
    assert reflect(cases[verdict])["verdict"] == verdict


def test_replan_is_gone():
    """FR-104 names exactly four terminal outcomes. Nothing may return a fifth."""
    for s in (state(failures=3, messages=[assistant_call(), tool_result(is_error=True)]),
              state(failures=99, messages=[assistant_call(), tool_result(is_error=True)])):
        assert reflect(s)["verdict"] in ("done", "stuck", "budget", "compact", "continue")
