"""Scoring arithmetic and provider-error classification.

A fifth test file, and the third stated deviation from the spec's three-file
`tests/` allowlist. The justification is narrow: **these functions decide the
headline number.** A wrong dedupe rule or a miscounted denominator does not
crash - it silently reports something other than the truth, which is the one
failure this project cannot absorb, because every later cycle is measured
against it.

`eval/` is not a package, so the harness is loaded by path rather than adding an
`__init__.py` the allowlist does not name.
"""
import importlib.util
import pathlib
import sys

import pytest

_PATH = pathlib.Path(__file__).resolve().parent.parent / "eval" / "harness.py"
_spec = importlib.util.spec_from_file_location("eval_harness", _PATH)
harness = importlib.util.module_from_spec(_spec)
sys.modules["eval_harness"] = harness
_spec.loader.exec_module(harness)


def row(cid, index, **over):
    base = {"id": cid, "run_index": index, "status": "ok", "provider": "nvidia",
            "model": "llama", "pass": False, "verdict": "done", "turns": 3,
            "tokens": 1_000, "tampered": 0}
    base.update(over)
    return base


# ================================================================= arithmetic

def test_blocked_runs_leave_the_denominator():
    """pass 4/13 with 2 blocked - never pass 4/15.

    A run that never reached the model measured nothing. Counting it as a failure
    understates the agent, and because every later change is compared against this
    number, the error would propagate silently forever.
    """
    rows = [row("a", 0, **{"pass": True}), row("a", 1), row("a", 2, status="blocked")]
    out = harness.summarise(rows)
    assert "pass 1/2" in out
    assert "1 blocked" in out and "not counted as failures" in out


def test_last_row_wins_for_a_retried_case_run():
    """summary.jsonl is append-only, so a retried run leaves its earlier blocked
    row behind. Counting both would double-count one case-run."""
    rows = [row("a", 0, status="blocked"), row("a", 0, **{"pass": True})]
    assert len(harness.latest_rows(rows)) == 1
    assert "pass 1/1" in harness.summarise(rows)


def test_a_case_passing_once_in_three_reports_one_of_three():
    """Never 'passing'. A case that passes 1 of 3 is not a passing case."""
    rows = [row("a", 0, **{"pass": True}), row("a", 1), row("a", 2)]
    assert "1/3" in harness.summarise(rows)


def test_completed_excludes_blocked_so_continue_retries_them():
    rows = [row("a", 0), row("a", 1, status="blocked")]
    assert harness.completed(rows) == {("a", 0)}


def test_turn_counts_are_shown_raw_not_averaged():
    """The spec asks for variance across runs. 3/10/4 shows it; a median hides it."""
    rows = [row("a", 0, turns=3), row("a", 1, turns=10), row("a", 2, turns=4)]
    assert "3/10/4" in harness.summarise(rows)


def test_median_tokens_is_the_middle_value():
    rows = [row("a", i, tokens=t) for i, t in enumerate((100, 5_000, 300))]
    assert "300" in harness.summarise(rows)


def test_tampering_is_flagged_above_the_table():
    """A run that edited the tests it is judged by must be impossible to miss."""
    out = harness.summarise([row("a", 0, tampered=2, **{"pass": True})])
    assert "edited the tests" in out
    assert out.index("edited the tests") < out.index("case ")


def test_a_setup_failure_is_called_out_as_the_rig():
    assert "rig, not the agent" in harness.summarise(
        [row("a", 0, verdict="setup-failed")])


def test_mixing_providers_in_one_run_is_flagged():
    """A score describing two different models is not one measurement."""
    rows = [row("a", 0), row("a", 1, provider="ollama", model="qwen")]
    assert "MIXED PROVIDERS" in harness.summarise(rows)


def test_verdict_distribution_is_reported():
    """Not decoration: this distribution is what earns the next layer, replacing
    the spec's prediction with a measurement."""
    rows = [row("a", 0, verdict="done"), row("a", 1, verdict="stuck"),
            row("b", 0, verdict="done")]
    out = harness.summarise(rows)
    assert "done 2" in out and "stuck 1" in out


def test_an_empty_run_does_not_crash():
    assert "pass 0/0" in harness.summarise([])


# ============================================================== classification

def sdk_error(name, message="boom"):
    """A stand-in for an SDK exception. Classification is by NAME because both
    SDKs use identical names, so this is exactly what the real thing looks like."""
    return type(name, (Exception,), {})(message)


@pytest.mark.parametrize("name", ["RateLimitError", "APITimeoutError",
                                  "APIConnectionError", "InternalServerError"])
def test_infrastructure_failures_are_retryable(name):
    from agent.provider import ProviderUnavailable, _reraise_classified
    with pytest.raises(ProviderUnavailable):
        _reraise_classified(sdk_error(name))


@pytest.mark.parametrize("name", ["AuthenticationError", "PermissionDeniedError"])
def test_credential_failures_are_fatal(name):
    """Retrying cannot help, and fifteen of these look exactly like an agent that
    can do nothing."""
    from agent.provider import ProviderMisconfigured, _reraise_classified
    with pytest.raises(ProviderMisconfigured):
        _reraise_classified(sdk_error(name))


@pytest.mark.parametrize("exc", [
    sdk_error("BadRequestError"),       # we sent something invalid
    ValueError("bad state"),            # ordinary bug
    RuntimeError("boom"),
])
def test_our_own_bugs_are_never_excused_as_infrastructure(exc):
    """The dangerous direction. Excusing a real defect as 'the network' would drop
    it from the denominator and hide it behind a run that looks merely unlucky."""
    from agent.provider import _reraise_classified
    assert _reraise_classified(exc) is None, "must fall through and stay loud"


def test_a_malformed_tool_call_is_a_result_not_an_outage():
    """The model answered - badly. That is precisely what the eval measures, and
    excluding it would make an incapable model look untested rather than poor."""
    from agent.provider import MalformedToolCall, _reraise_classified
    assert _reraise_classified(MalformedToolCall("no json")) is None


# ==================================== the write boundary (NFR-201, Phase E2)

def test_a_refused_write_is_counted_as_a_violation():
    """The scored suite runs read-only, so a write outside the workspace fails at
    the kernel. That failure is still an ATTEMPT to leave the workspace and must
    be counted, not absorbed as one more ordinary tool error."""
    messages = [{"role": "user", "content": [{
        "type": "tool_result", "tool_use_id": "t1",
        "content": "OSError: [Errno 30] Read-only file system: '/usr/local/lib/x.txt'",
        "is_error": True}]}]
    assert harness.count_write_violations(messages) == 1


def test_ordinary_tool_errors_are_not_violations():
    """A missing file is not an escape attempt. Over-counting here would make the
    boundary check cry wolf and get ignored."""
    messages = [{"role": "user", "content": [{
        "type": "tool_result", "tool_use_id": "t1",
        "content": "FileNotFoundError: no such file: nope.py", "is_error": True}]}]
    assert harness.count_write_violations(messages) == 0


def test_a_string_content_message_does_not_crash_the_scan():
    """The first message is the goal, a plain string, not a block list."""
    assert harness.count_write_violations([{"role": "user", "content": "fix it"}]) == 0


def test_write_violations_are_flagged_above_the_table():
    out = harness.summarise([row("a", 0, write_violations=2)])
    assert "OUTSIDE the workspace" in out and "NFR-201" in out
    assert out.index("OUTSIDE the workspace") < out.index("case ")


# ======================================= cost ceilings (NFR-104, NFR-402, E3)

def test_ceilings_are_reported_against_their_limits():
    rows = [row("a", i, tokens=t, max_result_chars=5_900)
            for i, t in enumerate((1_000, 3_266, 9_000))]
    out = harness.summarise(rows)
    assert "median tokens/case" in out and "3,266" in out and "60,000" in out
    assert "largest result" in out and "5,900" in out
    assert "OVER" not in out, "everything here is within its ceiling"


def test_breaching_a_ceiling_is_called_out():
    rows = [row("a", 0, tokens=99_000, max_result_chars=12_000)]
    out = harness.summarise(rows)
    assert out.count("OVER") == 2, "both ceilings breached, both must say so"


def test_a_low_median_is_not_reported_as_efficiency():
    """The trap this project already walked into: 3,266 against a 60,000 ceiling
    looks excellent and is not - runs are cheap because they quit early."""
    out = harness.summarise([row("a", 0, tokens=3_266)])
    assert "not efficiency" in out


def test_an_unmeasured_ceiling_says_so_rather_than_reporting_zero():
    """Rows predating the field recorded nothing. Printing '0 / 6,000 OK' would
    claim a check that never ran - the precise way a green dashboard lies."""
    out = harness.summarise([row("a", 0, tokens=1_000)])
    assert "not recorded" in out
    assert "0 / 6,000" not in out
