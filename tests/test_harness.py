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


def test_a_404_on_the_model_endpoint_is_infrastructure_not_a_score():
    """A run that never reached the model measured nothing.

    Measured mid-cycle: a scored run died at turn 0 with 0 tokens on
    NotFoundError, and the same model answered a probe minutes later - the
    endpoint had blinked. Unclassified, it fell through to "a crashed agent is a
    real result" and was recorded as a failed case, understating the agent and
    corrupting the comparison, which is precisely what the standing rule forbids.

    Retryable rather than fatal on purpose: a genuinely wrong model name fails
    every attempt and is then excluded as blocked, which is visible and costs
    three fast 404s; treating it as fatal would abort a whole suite over a hiccup.
    """
    from agent.provider import ProviderUnavailable, _reraise_classified
    with pytest.raises(ProviderUnavailable):
        _reraise_classified(sdk_error("NotFoundError"))


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


# ================================== the write boundary (NFR-201, Phase E2/K)

def _refusal(path):
    return [{"role": "user", "content": [{
        "type": "tool_result", "tool_use_id": "t1",
        "content": f"OSError: [Errno 30] Read-only file system: '{path}'",
        "is_error": True}]}]


def test_a_refused_write_is_counted_as_a_violation():
    """The scored suite runs read-only, so a write outside the declared roots fails
    at the kernel. That failure is still an ATTEMPT to leave the workspace and must
    be counted, not absorbed as one more ordinary tool error."""
    assert harness.write_violations(_refusal("/usr/local/lib/x.txt")) == [
        "/usr/local/lib/x.txt"]


def test_a_violation_names_the_path_it_targeted():
    """Phase K made the project tree read-only, so writes to the harness and to the
    fixtures now REACH this counter for the first time. A bare count cannot tell
    `/usr` (an agent confused about where it lives) from `eval/fixtures` (an agent
    reaching for the thing that grades it), and only one of those is alarming."""
    target = "/app/eval/fixtures/real-rich/tests/test_console.py"
    assert harness.write_violations(_refusal(target)) == [target]


def test_a_refusal_that_names_no_path_is_still_counted():
    """Never drop a violation because it was phrased unexpectedly. Unknown is a
    worse answer than a path, and a far better one than silence."""
    messages = [{"role": "user", "content": [{
        "type": "tool_result", "tool_use_id": "t1",
        "content": "OSError: Read-only file system", "is_error": True}]}]
    assert harness.write_violations(messages) == ["?"]


def test_ordinary_tool_errors_are_not_violations():
    """A missing file is not an escape attempt. Over-counting here would make the
    boundary check cry wolf and get ignored."""
    messages = [{"role": "user", "content": [{
        "type": "tool_result", "tool_use_id": "t1",
        "content": "FileNotFoundError: no such file: nope.py", "is_error": True}]}]
    assert harness.write_violations(messages) == []


def test_a_string_content_message_does_not_crash_the_scan():
    """The first message is the goal, a plain string, not a block list."""
    assert harness.write_violations([{"role": "user", "content": "fix it"}]) == []


def test_write_violations_are_flagged_above_the_table():
    out = harness.summarise([row("a", 0, write_violations=2,
                                 write_violation_paths=["/app/eval/tasks.jsonl"])])
    assert "OUTSIDE the workspace" in out and "NFR-201" in out
    # The path belongs in the warning, not only in a trace nobody opens.
    assert "/app/eval/tasks.jsonl" in out
    assert out.index("OUTSIDE the workspace") < out.index("case ")


def test_an_older_run_without_paths_still_summarises():
    """Rows written before Phase K carry no `write_violation_paths`. Re-reading an
    old run directory must not crash on the field that did not exist yet."""
    assert "NFR-201" in harness.summarise([row("a", 0, write_violations=1)])


# ================================ the two declared writable roots (Phase K)

def test_the_agent_home_is_outside_the_workspace():
    """It exists precisely BECAUSE reset.sh wipes the workspace between runs. Put
    it back inside and memory silently stops surviving - with no test failing and
    no error, which is exactly how this property would get lost."""
    from agent import config

    assert config.WORKSPACE not in config.STATE_DB.parents


def test_reset_leaves_the_agent_home_alone(tmp_path):
    """The end-to-end version of the property above, against the real script.

    Skipped where there is no bash. That is honest rather than sufficient - the
    scored runs execute this script inside the container, where bash always exists.
    """
    import os
    import shutil
    import subprocess

    bash = shutil.which("bash")
    if not bash:
        pytest.skip("reset.sh needs bash")

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "scratch.txt").write_text("left over from the last case")
    home = tmp_path / "state"
    home.mkdir()
    (home / "memory.db").write_text("learned last week")

    repo = pathlib.Path(__file__).resolve().parent.parent
    case = next(p.name for p in (repo / "eval" / "fixtures").iterdir() if p.is_dir())
    done = subprocess.run(
        [bash, str(repo / "scripts" / "reset.sh"), case],
        env={**os.environ, "AGENT_WORKSPACE": str(workspace)},
        capture_output=True, text=True)

    assert done.returncode == 0, done.stderr
    assert not (workspace / "scratch.txt").exists(), "the workspace should be wiped"
    assert (home / "memory.db").read_text() == "learned last week"


def test_each_scored_case_run_gets_a_blank_agent_home(tmp_path, monkeypatch):
    """A memory carried from case 1 into case 2 is the same contamination that
    forced one container per case-run: `missing-dep` would pass on the repeat
    without the agent doing anything."""
    monkeypatch.setattr(harness, "REPO", tmp_path)
    home = harness.agent_home({"id": "fix-import"}, 0)
    (home / "memory.db").write_text("what run 0 learned")

    assert harness.agent_home({"id": "fix-import"}, 1) != home
    assert not (harness.agent_home({"id": "fix-import"}, 0) / "memory.db").exists()


# ============================================= per-session egress (Phase K4)

def test_a_case_that_declares_nothing_reaches_only_the_model():
    """Repo work needs no web access, and the default must stay that narrow."""
    assert harness.allowlist_for({"id": "fix-import"}) == harness.model_hosts()


def test_a_declared_host_widens_only_the_case_that_declared_it():
    """`egress` on a tasks.jsonl row is per case, so a research task cannot quietly
    hand its allowlist to the repo cases that run after it."""
    widened = harness.allowlist_for({"id": "research", "egress": ["docs.python.org"]})
    assert "docs.python.org" in widened
    assert set(harness.model_hosts()) <= set(widened)
    assert "docs.python.org" not in harness.allowlist_for({"id": "fix-import"})


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

def test_failing_tests_reads_the_pytest_summary():
    """Pass/fail hides everything short of a green suite.

    Measured on the first real-repository baseline: one case fixed five of its six
    failures on two of three runs and was recorded identically to a run that did
    nothing, while another broke 38 previously-passing tests and was likewise
    recorded as a plain 0. On a set the agent scores zero on, this delta is the
    only signal there is.
    """
    assert harness.failing_tests("4 failed, 689 passed, 69 skipped in 4.13s") == 4
    assert harness.failing_tests("693 passed, 69 skipped in 3.67s") == 0
    assert harness.failing_tests("[31m6 failed[0m, 1857 passed") == 6, "ANSI colour must not hide it"


def test_failing_tests_distinguishes_green_from_unreadable():
    """0 and None must never be conflated - that would invent progress."""
    assert harness.failing_tests("12 passed") == 0
    assert harness.failing_tests("") is None
    assert harness.failing_tests("Interrupted: no summary line") is None


def test_progress_column_shows_partial_credit():
    group = [{"failures_before": 6, "failures_after": a, "run_index": i}
             for i, a in enumerate([1, 1, 6])]
    assert harness._progress(group) == "6->1/1/6"
    assert harness._progress([{"failures_before": None, "failures_after": None}]) == "-"


# ================================================== FR-804: delta between runs

from eval.harness import delta


def _row(cid, run_index, passed, tokens=10_000, status="ok"):
    return {"id": cid, "run_index": run_index, "pass": passed, "status": status,
            "tokens": tokens, "verdict": "done" if passed else "stuck"}


def test_delta_reports_the_move_and_names_only_the_cases_that_changed():
    """A table where fourteen rows say 3/3 -> 3/3 buries the one that regressed."""
    before = [_row("alpha", 0, True), _row("alpha", 1, True),
              _row("beta", 0, False), _row("beta", 1, False)]
    now = [_row("alpha", 0, True), _row("alpha", 1, True),
           _row("beta", 0, True), _row("beta", 1, False)]

    out = delta(now, before, "20260101T000000Z")

    assert "pass 2/4 -> 3/4   (+1)" in out
    assert "beta" in out, "the case that moved is named"
    assert "alpha" not in out, "the case that did not move is not"


def test_delta_reports_a_regression_with_its_sign():
    before = [_row("alpha", 0, True), _row("alpha", 1, True)]
    now = [_row("alpha", 0, True), _row("alpha", 1, False)]

    out = delta(now, before, "prev")

    assert "(-1)" in out and "-1" in out


def test_delta_reports_cost_even_when_the_score_holds():
    """A pass rate held at the same number for 30% more tokens is a regression
    that no pass/fail column shows."""
    before = [_row("alpha", 0, True, tokens=10_000)]
    now = [_row("alpha", 0, True, tokens=13_000)]

    out = delta(now, before, "prev")

    assert "tokens (median)" in out
    assert "+30%" in out


def test_delta_flags_a_changed_population_rather_than_hiding_it():
    """Comparing a 5-case run against a 6-case run is two numbers printed next to
    each other, not a delta."""
    before = [_row("alpha", 0, True), _row("gone", 0, True)]
    now = [_row("alpha", 0, True), _row("fresh", 0, False)]

    out = delta(now, before, "prev")

    assert "only in the previous run: gone" in out
    assert "new in this run: fresh" in out


def test_delta_is_empty_when_there_is_nothing_to_compare():
    assert delta([_row("alpha", 0, True)], [], "prev") == ""


def test_delta_ignores_blocked_runs():
    """A blocked run measured nothing and must not count in either direction -
    the standing rule that keeps `pass 4/13, 2 blocked` from becoming `4/15`."""
    before = [_row("alpha", 0, True)]
    now = [_row("alpha", 0, True), _row("alpha", 1, False, status="blocked")]

    assert "pass 1/1 -> 1/1   (+0)" in delta(now, before, "prev")
