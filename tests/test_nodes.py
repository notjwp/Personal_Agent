"""The deterministic nodes, the three tools, and the assembled loop.

Everything here runs without an API key and without a network. The loop test drives
the real graph end to end against a stand-in model, so routing, the gate, output
trimming, the failure counter and checkpointing are all proven before a single
token is spent.

This file carries nodes, tools and the loop together rather than splitting into
more files, because the build spec's test allowlist names only three and this is
already one stated deviation.
"""
import json
from types import SimpleNamespace

import pytest

from agent import config
from agent.graph import execute, finish, gate
from agent.provider import Reply
import subprocess
import time

from agent.tools import edit_file, read_file, run_shell, write_file


# ===================================================================== helpers

def state(**over):
    base = {
        "messages": [{"role": "user", "content": "fix it"}],
        "turns": 0, "max_turns": 12,
        "spent_tokens": 0, "budget_tokens": 200_000,
        "failures": 0, "verdict": None, "approved": [], "denied": [],
    }
    base.update(over)
    return base


def call(name, cid="t1", **args):
    return {"id": cid, "name": name, "input": args}


def assistant_calls(*calls):
    return {"role": "assistant", "content": [
        {"type": "tool_use", "id": c["id"], "name": c["name"], "input": c["input"]}
        for c in calls]}


def cfg(autonomous=True, trace=None):
    return {"configurable": {"autonomous": autonomous, "trace": trace}}


# ======================================================================== gate

def test_gate_approves_a_safe_call(tmp_workspace):
    s = state(messages=[assistant_calls(call("read_file", path="a.py"))])
    out = gate(s, cfg())
    assert len(out["approved"]) == 1 and out["denied"] == []


def test_gate_produces_mixed_verdicts_in_one_turn(tmp_workspace):
    """Mixed verdicts within a single turn are legal: some approved, others denied."""
    s = state(messages=[assistant_calls(
        call("read_file", cid="ok", path="a.py"),
        call("write_file", cid="bad", path="../escape.txt", content="x"),
    )])
    out = gate(s, cfg())
    assert [c["id"] for c in out["approved"]] == ["ok"]
    assert [c["id"] for c in out["denied"]] == ["bad"]
    assert "escapes workspace" in out["denied"][0]["reason"]


def test_gate_has_no_side_effects(tmp_workspace):
    """The gate re-executes from its first line on resume, so running it twice must
    change nothing on disk and produce an identical result."""
    s = state(messages=[assistant_calls(
        call("run_shell", command="rm -rf /"),
        call("read_file", cid="t2", path="a.py"),
    )])
    before = sorted(p.name for p in tmp_workspace.rglob("*"))
    first = gate(s, cfg())
    second = gate(s, cfg())
    assert first == second
    assert sorted(p.name for p in tmp_workspace.rglob("*")) == before


def test_gate_denies_destructive_when_autonomous(tmp_workspace):
    s = state(messages=[assistant_calls(call("run_shell", command="rm -rf /"))])
    assert gate(s, cfg(autonomous=True))["denied"]


# ===================================================================== execute

def test_execute_runs_an_approved_call(tmp_workspace):
    (tmp_workspace / "a.py").write_text("hello\n")
    out = execute(state(approved=[call("read_file", path="a.py")]), cfg())
    result = out["messages"][-1]["content"][0]
    assert result["is_error"] is False and "hello" in result["content"]
    assert out["turns"] == 1


def test_execute_converts_tool_exception_to_observation(tmp_workspace):
    """A tool exception must never propagate out of the node."""
    out = execute(state(approved=[call("read_file", path="nope.py")]), cfg())
    result = out["messages"][-1]["content"][0]
    assert result["is_error"] is True
    assert "FileNotFoundError" in result["content"] or "No such file" in result["content"]


def test_execute_emits_all_results_in_one_user_message(tmp_workspace):
    (tmp_workspace / "a.py").write_text("x\n")
    out = execute(state(approved=[
        call("read_file", cid="t1", path="a.py"),
        call("read_file", cid="t2", path="a.py"),
    ]), cfg())
    assert len(out["messages"][-1]["content"]) == 2, "splitting results trains the model badly"


def test_denied_call_becomes_a_synthetic_error_observation(tmp_workspace):
    denied = {**call("write_file", path="../x", content="y"), "reason": "path escapes workspace"}
    out = execute(state(denied=[denied]), cfg())
    result = out["messages"][-1]["content"][0]
    assert result["is_error"] is True
    assert "Denied by policy" in result["content"]
    assert out["failures"] == 1


def test_failures_counter_resets_on_a_clean_turn(tmp_workspace):
    """Correction (c): a plain int with overwrite semantics, not an accumulating list."""
    (tmp_workspace / "a.py").write_text("x\n")
    assert execute(state(failures=2, approved=[call("read_file", path="a.py")]),
                   cfg())["failures"] == 0


def test_failures_counter_increments_on_a_failed_turn(tmp_workspace):
    assert execute(state(failures=1, approved=[call("read_file", path="nope.py")]),
                   cfg())["failures"] == 2


def test_oversized_shell_output_is_spilled(tmp_workspace):
    """Shell output is ephemeral, so overflow must spill to an artifact (FR-401/402).

    This was written against read_file. read_file now sizes its own window rather
    than overflowing, and spilling IT was always redundant: the "full output"
    artifact was a copy of a file already sitting in the workspace, which the agent
    can page or grep directly. Command output has no second copy, so this is where
    the spill mechanism actually earns its place.
    """
    big = chr(10).join(f"line {i}" for i in range(5000))
    (tmp_workspace / "big.txt").write_text(big)
    out = execute(state(approved=[call("run_shell", command="cat big.txt")]), cfg())
    body = out["messages"][-1]["content"][0]["content"]
    assert len(body) < config.MAX_RESULT_CHARS + 600
    assert "[full output: " in body
    assert list(config.ARTIFACTS.glob("*.txt"))


def test_oversized_read_narrows_instead_of_spilling(tmp_workspace):
    """The counterpart: a paged read returns fewer lines, all of them contiguous."""
    big = chr(10).join(f"line {i}" for i in range(5000))
    (tmp_workspace / "big.txt").write_text(big)
    out = execute(state(approved=[call("read_file", path="big.txt", limit=5000)]), cfg())
    body = out["messages"][-1]["content"][0]["content"]
    assert len(body) < config.MAX_RESULT_CHARS + 600
    assert "elided" not in body, "a paged read must not have its middle removed"
    assert "offset=" in body, "and must say how to fetch the next page"


def test_execute_records_a_trace_entry_per_call(tmp_workspace):
    """One structured record per tool call, denials included."""
    (tmp_workspace / "a.py").write_text("x\n")
    trace = []
    execute(state(approved=[call("read_file", path="a.py")],
                  denied=[{**call("run_shell", cid="d1", command="rm -rf /"),
                           "reason": "destructive"}]),
            cfg(trace=trace))
    assert [t["verdict"] for t in trace] == ["auto", "deny"]
    for entry in trace:
        assert {"tool", "argument_hash", "verdict", "duration_ms",
                "input_bytes", "output_bytes", "spill_path"} <= set(entry)


# ====================================================================== finish

def test_finish_records_the_terminal_verdict(tmp_workspace):
    trace = []
    finish(state(verdict="done", turns=4, spent_tokens=1234), cfg(trace=trace))
    assert trace[-1] == {"kind": "terminal", "verdict": "done",
                         "turns": 4, "spent_tokens": 1234}


# ======================================================================= tools

def test_read_file_honours_offset_and_limit(tmp_workspace):
    (tmp_workspace / "a.py").write_text("\n".join(f"line{i}" for i in range(100)))
    out = read_file("a.py", offset=10, limit=5)
    assert "line10" in out and "line14" in out
    assert "line15" not in out and "line9" not in out


def test_read_file_returns_a_contiguous_window_under_the_cap(tmp_workspace):
    """A paged read must return CONTIGUOUS lines, not head+tail with a hole.

    shrink() was built for UNEXPECTEDLY large output. Applied to a deliberately
    paged read it deletes the middle of the window the agent explicitly asked for.
    Measured on real-rich: rich/console.py is 101,228 chars, a read_file(limit=500)
    renders 18,920 chars, the cap is 6,000, and what arrived was 30 head + 20 tail
    of the 500 lines requested. Seeing the whole file took 54 reads, and the agent
    edited a file it had only ever seen in fragments.

    Sizing the window inside read_file keeps NFR-104 intact - the result still fits
    under the cap - while making paging actually work.
    """
    body = [f"line{i:04d} " + "x" * 60 for i in range(2000)]
    (tmp_workspace / "big.py").write_text(chr(10).join(body), encoding="utf-8")

    out = read_file("big.py", offset=0, limit=500)
    assert len(out) <= config.TOOL_CAPS["read_file"], (
        f"read_file must size its own window; got {len(out)} chars")
    assert "elided" not in out, "a paged read must not have its middle removed"

    shown = [ln for ln in out.splitlines() if "line0" in ln or "line1" in ln]
    numbers = [int(ln.split("line")[1][:4]) for ln in shown if "line" in ln]
    assert numbers == list(range(numbers[0], numbers[0] + len(numbers))), (
        "the delivered lines must be consecutive")
    assert len(numbers) > 50, f"expected a useful window, got {len(numbers)} lines"


def test_read_file_says_where_to_continue_when_it_narrows(tmp_workspace):
    """An error the model cannot act on costs a turn; so does a silent truncation."""
    body = [f"line{i:04d} " + "y" * 60 for i in range(2000)]
    (tmp_workspace / "big.py").write_text(chr(10).join(body), encoding="utf-8")
    out = read_file("big.py", offset=0, limit=500)
    assert "offset=" in out, "the agent must be told how to fetch the next page"


def test_read_file_small_file_is_unchanged(tmp_workspace):
    """Narrowing must only apply when the window would overflow."""
    (tmp_workspace / "s.py").write_text(chr(10).join(f"l{i}" for i in range(10)),
                                        encoding="utf-8")
    out = read_file("s.py", offset=0, limit=500)
    assert "l0" in out and "l9" in out
    assert "offset=" not in out, "a complete read must not suggest a next page"


def test_write_file_creates_parent_directories(tmp_workspace):
    write_file("deep/nested/a.py", "x = 1\n")
    assert (tmp_workspace / "deep" / "nested" / "a.py").read_text() == "x = 1\n"


def test_write_file_replaces_entirely(tmp_workspace):
    write_file("a.py", "first\n")
    write_file("a.py", "second\n")
    assert (tmp_workspace / "a.py").read_text() == "second\n"


def test_edit_file_replaces_a_unique_string(tmp_workspace):
    """Targeted replacement, so a small fix costs a small number of tokens.

    Measured cause of the 0/18 real-repository baseline: `write_file` replaces a
    file entirely, so changing five lines meant emitting the whole file inside
    MAX_TOKENS (16,000, covering thinking + text + tool arguments). Real files run
    559-2,689 lines; rich/console.py needs ~25,308 tokens to rewrite, which is 158%
    of one reply - impossible, not merely expensive. Across 30 runs the agent made
    11 writes against 352 reads.
    """
    (tmp_workspace / "m.py").write_text("def f():" + chr(10) + "    return 1" + chr(10), encoding="utf-8")
    out = edit_file("m.py", "return 1", "return 2")
    assert (tmp_workspace / "m.py").read_text() == "def f():" + chr(10) + "    return 2" + chr(10)
    assert "m.py" in out


def test_edit_file_refuses_an_ambiguous_match(tmp_workspace):
    """Two matches means the agent does not know which one it is changing.

    Refusing is the whole safety property of exact matching: silently editing the
    first occurrence would corrupt a file in a way no test necessarily catches.
    """
    (tmp_workspace / "m.py").write_text("x = 1" + chr(10) + "y = 1" + chr(10), encoding="utf-8")
    with pytest.raises(ValueError) as caught:
        edit_file("m.py", "= 1", "= 2")
    message = str(caught.value)
    assert "twice" in message or "2 times" in message
    assert "more" in message.lower(), "the error must say HOW to disambiguate"
    assert (tmp_workspace / "m.py").read_text() == "x = 1" + chr(10) + "y = 1" + chr(10), "must not edit"


def test_edit_file_says_what_to_do_when_the_string_is_absent(tmp_workspace):
    """An error the model cannot act on costs a turn every time it is retried."""
    (tmp_workspace / "m.py").write_text("x = 1" + chr(10), encoding="utf-8")
    with pytest.raises(ValueError) as caught:
        edit_file("m.py", "nope", "yes")
    assert "read_file" in str(caught.value)


def test_run_shell_separates_streams_and_exit_code(tmp_workspace):
    out = run_shell("echo out; echo err >&2; exit 3")
    assert "exit code: 3" in out
    assert "--- stdout ---" in out and "out" in out
    assert "--- stderr ---" in out and "err" in out


def test_run_shell_timeout_kills_the_whole_process_tree(tmp_workspace):
    """A timeout must actually bound the call (FR-202).

    Written while chasing a 25-minute hang on a real repository. The hang turned
    out to be in the HARNESS - its scored check command had no timeout at all -
    and NOT in this tool, whose timeout works. The test is kept anyway: an
    unbounded run_shell would hang a live session just as badly, and nothing else
    covered it.

    The command is COMPOUND on purpose. With a single command `sh -c "python ..."`
    the shell execs into python, so killing the child kills everything; a forked
    grandchild is the harder case and the one worth guarding.
    """
    started = time.monotonic()
    with pytest.raises(subprocess.TimeoutExpired):
        run_shell("cd / && python -c 'import time; time.sleep(60)'", timeout=2)
    elapsed = time.monotonic() - started
    assert elapsed < 20, f"timeout did not bound the call: took {elapsed:.0f}s"


def test_run_shell_runs_in_the_workspace(tmp_workspace):
    (tmp_workspace / "marker.txt").write_text("here")
    assert "marker.txt" in run_shell("ls")


def test_tools_raise_rather_than_returning_error_strings(tmp_workspace):
    with pytest.raises(FileNotFoundError):
        read_file("absent.py")


# ============================================== the assembled loop, offline

def tool_turn(name, cid="t1", **args):
    return Reply(blocks=[{"type": "tool_use", "id": cid, "name": name, "input": args}],
                 billed_tokens=150, cache_read_tokens=0, stop_reason="tool_use")


def text_turn(text):
    return Reply(blocks=[{"type": "text", "text": text}],
                 billed_tokens=150, cache_read_tokens=0, stop_reason="end_turn")


def use_fake(monkeypatch, script):
    """Replace the provider seam, not a vendor SDK.

    Patching `call_model` rather than `anthropic.Anthropic` keeps these tests true
    for whichever provider is configured - the loop's behaviour is not supposed to
    depend on who answered.
    """
    script = list(script)
    seen = []

    def fake_call_model(messages, system, tools, on_text=None):
        seen.append({"messages": list(messages), "system": system, "tools": tools})
        if not script:
            raise AssertionError("act called the model more times than the script allows")
        return script.pop(0)

    monkeypatch.setattr("agent.graph.call_model", fake_call_model)
    return seen


def run(app, thread="offline", **over):
    return app.invoke(state(**over), {"configurable": {
        "thread_id": thread, "autonomous": True, "trace": []}})


def test_full_loop_offline(fresh_app, tmp_workspace, monkeypatch):
    """read -> write -> shell -> text-only -> done, with no network and no key."""
    (tmp_workspace / "broken.py").write_text("x = \n")
    seen = use_fake(monkeypatch, [
        tool_turn("read_file", path="broken.py"),
        tool_turn("write_file", cid="t2", path="broken.py", content="x = 1\n"),
        tool_turn("run_shell", cid="t3", command="echo ok"),
        text_turn("Fixed. Tests pass."),
    ])

    final = run(fresh_app, "loop-1")

    assert final["verdict"] == "done"
    assert final["turns"] == 3
    assert final["failures"] == 0
    assert final["spent_tokens"] == 600          # 4 turns x 150 billed
    assert (tmp_workspace / "broken.py").read_text() == "x = 1\n"

    assert len(seen) == 4, "one model call per turn"
    for req in seen:
        assert req["tools"], "tools must be sent every turn"
        assert "You fix broken code" in req["system"], "the system prompt must be sent"
    assert len(seen[-1]["messages"]) > len(seen[0]["messages"]), "history accumulates"


def test_text_only_first_turn_does_not_finish(fresh_app, tmp_workspace, monkeypatch):
    """Correction (b) end to end: a preamble must not terminate the run."""
    use_fake(monkeypatch, [
        text_turn("Let me look at the test file first."),
        tool_turn("run_shell", command="echo ok"),
        text_turn("Done."),
    ])
    final = run(fresh_app, "loop-2")
    assert final["verdict"] == "done"
    assert final["turns"] == 1, "the run continued past the preamble"


def test_denied_call_keeps_the_loop_running(fresh_app, tmp_workspace, monkeypatch):
    use_fake(monkeypatch, [
        tool_turn("write_file", path="../escape.txt", content="pwned"),
        tool_turn("run_shell", cid="t2", command="echo recovered"),
        text_turn("Recovered."),
    ])
    final = run(fresh_app, "loop-3")
    assert final["verdict"] == "done"
    assert not (tmp_workspace.parent / "escape.txt").exists(), "wrote outside the workspace"


def test_tool_exception_does_not_crash_the_run(fresh_app, tmp_workspace, monkeypatch):
    use_fake(monkeypatch, [
        tool_turn("read_file", path="does-not-exist.py"),
        text_turn("That file is missing."),
    ])
    final = run(fresh_app, "loop-4")
    assert final["verdict"] == "done"
    assert final["failures"] == 1


def test_thrashing_is_detected(fresh_app, tmp_workspace, monkeypatch):
    """The same call three times running terminates as stuck."""
    use_fake(monkeypatch, [tool_turn("run_shell", command="ls") for _ in range(4)])
    final = run(fresh_app, "loop-5")
    assert final["verdict"] == "stuck"


def test_turn_cap_terminates(fresh_app, tmp_workspace, monkeypatch):
    use_fake(monkeypatch, [
        tool_turn("run_shell", cid=f"t{i}", command=f"echo {i}") for i in range(10)
    ])
    final = run(fresh_app, "loop-6", max_turns=3)
    assert final["verdict"] == "stuck"
    assert final["turns"] == 3


def test_budget_exhaustion_terminates(fresh_app, tmp_workspace, monkeypatch):
    use_fake(monkeypatch, [
        tool_turn("run_shell", cid=f"t{i}", command=f"echo {i}") for i in range(10)
    ])
    final = run(fresh_app, "loop-7", budget_tokens=200)
    assert final["verdict"] == "compact", "over 60% of budget terminates at v1"


def test_trace_captures_model_and_tool_activity(fresh_app, tmp_workspace, monkeypatch):
    use_fake(monkeypatch, [
        tool_turn("run_shell", command="echo ok"),
        text_turn("Done."),
    ])
    trace = []
    fresh_app.invoke(state(), {"configurable": {
        "thread_id": "loop-8", "autonomous": True, "trace": trace}})
    kinds = [t["kind"] for t in trace]
    assert kinds.count("model") == 2
    assert kinds.count("tool") == 1
    assert kinds[-1] == "terminal"
    # Found by kind, not by position: `act` emits a `memory` event ahead of the
    # model one whenever anything was recalled, so trace[0] is not always the model.
    assert next(t for t in trace if t["kind"] == "model")["billed_tokens"] == 150


def test_checkpoint_persists_and_resumes(fresh_app, tmp_workspace, monkeypatch):
    """A task's identity is its thread_id; state survives the invocation."""
    use_fake(monkeypatch, [
        tool_turn("run_shell", command="echo ok"),
        text_turn("Done."),
    ])
    cfg_ = {"configurable": {"thread_id": "resume-me", "autonomous": True, "trace": []}}
    fresh_app.invoke(state(), cfg_)

    saved = fresh_app.get_state(cfg_).values
    assert saved["verdict"] == "done"
    assert saved["turns"] == 1
    assert len(saved["messages"]) > 1, "history was checkpointed, not discarded"


# ========================================== provider translation (no network)

from agent.provider import (MalformedToolCall, from_openai_message,
                            to_openai_messages, to_openai_tools)


class _FakeCall:
    def __init__(self, cid, name, arguments):
        self.id = cid
        self.function = SimpleNamespace(name=name, arguments=arguments)


class _FakeMessage:
    def __init__(self, content=None, tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls


def test_tool_schema_translation():
    out = to_openai_tools([{"name": "read_file", "description": "Read it.",
                            "input_schema": {"type": "object", "properties": {}}}])
    assert out == [{"type": "function", "function": {
        "name": "read_file", "description": "Read it.",
        "parameters": {"type": "object", "properties": {}}}}]


def test_system_prompt_becomes_the_first_message():
    out = to_openai_messages("BE GOOD", [{"role": "user", "content": "hi"}])
    assert out[0] == {"role": "system", "content": "BE GOOD"}


def test_assistant_turn_splits_into_content_and_tool_calls():
    out = to_openai_messages("s", [{"role": "assistant", "content": [
        {"type": "text", "text": "Looking."},
        {"type": "tool_use", "id": "t1", "name": "run_shell", "input": {"command": "ls"}},
    ]}])
    assistant = out[-1]
    assert assistant["content"] == "Looking."
    assert assistant["tool_calls"][0]["id"] == "t1"
    assert assistant["tool_calls"][0]["function"]["name"] == "run_shell"
    # arguments travel as a JSON STRING, not an object
    assert json.loads(assistant["tool_calls"][0]["function"]["arguments"]) == {"command": "ls"}


def test_tool_results_fan_out_into_one_message_each():
    """The asymmetry that breaks a naive port: our single user message carrying N
    results must become N separate `role: tool` messages, in order."""
    out = to_openai_messages("s", [{"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "t1", "content": "first"},
        {"type": "tool_result", "tool_use_id": "t2", "content": "second"},
    ]}])
    tools = [m for m in out if m["role"] == "tool"]
    assert len(tools) == 2, "results must not be collapsed into one message"
    assert [m["tool_call_id"] for m in tools] == ["t1", "t2"]
    assert [m["content"] for m in tools] == ["first", "second"]


def test_anthropic_only_blocks_are_dropped():
    """Thinking blocks are meaningless to an OpenAI-compatible endpoint."""
    out = to_openai_messages("s", [{"role": "assistant", "content": [
        {"type": "thinking", "thinking": "", "signature": "abc"},
        {"type": "text", "text": "Hello."},
    ]}])
    assert out[-1]["content"] == "Hello."
    assert "thinking" not in json.dumps(out)


def test_reply_translation_back_to_blocks():
    blocks = from_openai_message(_FakeMessage(
        content="On it.",
        tool_calls=[_FakeCall("t1", "read_file", '{"path": "a.py"}')]))
    assert blocks[0] == {"type": "text", "text": "On it."}
    assert blocks[1] == {"type": "tool_use", "id": "t1", "name": "read_file",
                         "input": {"path": "a.py"}}


def test_round_trip_survives_a_full_turn():
    original = [
        {"role": "user", "content": "fix it"},
        {"role": "assistant", "content": [
            {"type": "tool_use", "id": "t1", "name": "read_file", "input": {"path": "a.py"}},
            {"type": "tool_use", "id": "t2", "name": "run_shell", "input": {"command": "ls"}},
        ]},
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "t1", "content": "ok"},
            {"type": "tool_result", "tool_use_id": "t2", "content": "ok"},
        ]},
    ]
    out = to_openai_messages("s", original)
    assert [m["role"] for m in out] == ["system", "user", "assistant", "tool", "tool"]


def test_malformed_tool_arguments_fail_loudly():
    """Native tool calling is a hard requirement with no fallback, so a model that
    emits unparseable arguments must be obvious, not silently coerced."""
    with pytest.raises(MalformedToolCall):
        from_openai_message(_FakeMessage(
            tool_calls=[_FakeCall("t1", "read_file", '{"path": ')]))


def test_non_object_tool_arguments_fail_loudly():
    with pytest.raises(MalformedToolCall):
        from_openai_message(_FakeMessage(
            tool_calls=[_FakeCall("t1", "read_file", '"just a string"')]))


def test_run_shell_accepts_a_string_timeout(tmp_workspace):
    """Observed live: the model sent {"timeout": "120"} on 2 of 5 calls, and
    subprocess.run rejected the string. Schema declarations are not enforcement."""
    assert "exit code: 0" in run_shell("echo hi", timeout="30")


# ==================================================== approval (human in loop)
#
# The design splits checking (gate) from doing (execute) so that a tool cannot
# fire twice when a suspended run resumes. That claim has been true on paper
# since the loop was built and had never been executed once: every run so far
# used autonomous=True, where a `confirm` verdict becomes a refusal and nothing
# ever pauses. These tests are the proof, and they run offline.

from langgraph.types import Command

from agent.tools import TOOLS


def interactive(thread, trace=None):
    """Config for a session that is allowed to pause.

    autonomous=False is the switch that makes `confirm` suspend rather than
    refuse. thread_id is required: without it the checkpointer has nowhere to
    store the paused state, so the run cannot be resumed.
    """
    return {"configurable": {"thread_id": thread, "autonomous": False,
                             "trace": trace if trace is not None else []}}


def spy_on_run_shell(monkeypatch):
    """Swap the tool function for a recorder, so 'exactly once' is measured
    rather than assumed."""
    calls = []
    monkeypatch.setitem(
        TOOLS["run_shell"], "fn",
        lambda **kw: calls.append(kw) or "exit code: 0\nstdout:\nstderr:\n")
    return calls


def two_tool_turn(*specs):
    """One assistant turn carrying several tool_use blocks."""
    return Reply(
        blocks=[{"type": "tool_use", "id": cid, "name": name, "input": args}
                for cid, name, args in specs],
        billed_tokens=150, cache_read_tokens=0, stop_reason="tool_use")


DESTRUCTIVE = {"command": "rm -rf build"}


def test_destructive_call_pauses_instead_of_running(fresh_app, tmp_workspace, monkeypatch):
    calls = spy_on_run_shell(monkeypatch)
    use_fake(monkeypatch, [tool_turn("run_shell", **DESTRUCTIVE), text_turn("Done.")])

    out = fresh_app.invoke(state(), interactive("pause-1"))

    assert "__interrupt__" in out, "a destructive command must pause"
    assert calls == [], "nothing may run before approval"
    payload = out["__interrupt__"][0].value
    # FR-306: the prompt needs the full argument set, so the gate must hand it over.
    assert payload["call"]["input"] == DESTRUCTIVE
    assert "destructive" in payload["reason"]


def test_approved_tool_runs_exactly_once(fresh_app, tmp_workspace, monkeypatch):
    """CE-07, executed at last.

    The gate re-runs from its first line on resume. Were gate and execute one
    node, the tool would fire again every time - silently, and only in the
    interactive path that no scored run exercises.
    """
    calls = spy_on_run_shell(monkeypatch)
    use_fake(monkeypatch, [tool_turn("run_shell", **DESTRUCTIVE), text_turn("Done.")])
    cfg_ = interactive("approve-once")

    fresh_app.invoke(state(), cfg_)
    out = fresh_app.invoke(Command(resume="allow"), cfg_)

    assert len(calls) == 1, f"tool fired {len(calls)} times, expected exactly 1"
    assert calls[0] == DESTRUCTIVE
    assert out["verdict"] == "done"


def test_rejected_tool_never_runs(fresh_app, tmp_workspace, monkeypatch):
    """A denial is an observation the model has to route around, not a crash."""
    calls = spy_on_run_shell(monkeypatch)
    use_fake(monkeypatch, [tool_turn("run_shell", **DESTRUCTIVE), text_turn("Understood.")])
    cfg_ = interactive("reject-1")

    fresh_app.invoke(state(), cfg_)
    out = fresh_app.invoke(Command(resume="deny"), cfg_)

    assert calls == [], "a rejected call must never execute"
    observation = out["messages"][-2]["content"][0]
    assert observation["is_error"] is True
    assert "rejected by user" in observation["content"]


def test_two_approvals_in_one_turn_are_answered_separately(
        fresh_app, tmp_workspace, monkeypatch):
    """Two destructive calls produce two pauses, resumed one at a time. Allowing
    the first and denying the second must run exactly the first."""
    calls = spy_on_run_shell(monkeypatch)
    use_fake(monkeypatch, [
        two_tool_turn(("t1", "run_shell", {"command": "rm -rf build"}),
                      ("t2", "run_shell", {"command": "rm -rf dist"})),
        text_turn("Done."),
    ])
    cfg_ = interactive("two-calls")

    first = fresh_app.invoke(state(), cfg_)
    assert first["__interrupt__"][0].value["call"]["input"]["command"] == "rm -rf build"

    second = fresh_app.invoke(Command(resume="allow"), cfg_)
    assert "__interrupt__" in second, "the second call needs its own approval"
    assert second["__interrupt__"][0].value["call"]["input"]["command"] == "rm -rf dist"

    fresh_app.invoke(Command(resume="deny"), cfg_)

    assert [c["command"] for c in calls] == ["rm -rf build"]


@pytest.mark.parametrize("answer", ["yes", "y", "", "ALLOW", "sure"])
def test_a_garbled_answer_fails_closed(fresh_app, tmp_workspace, monkeypatch, answer):
    """Only the exact string "allow" is consent. Anything else is a rejection,
    so a mangled or truncated answer can never green-light a destructive call."""
    calls = spy_on_run_shell(monkeypatch)
    use_fake(monkeypatch, [tool_turn("run_shell", **DESTRUCTIVE), text_turn("ok")])
    cfg_ = interactive(f"garbled-{answer or 'empty'}")

    fresh_app.invoke(state(), cfg_)
    fresh_app.invoke(Command(resume=answer), cfg_)

    assert calls == [], f"answer {answer!r} was treated as consent"


def test_autonomous_mode_never_pauses(fresh_app, tmp_workspace, monkeypatch):
    """The invariant every scored run depends on: unattended, a `confirm`
    verdict becomes a denial and the run never blocks waiting for a human."""
    calls = spy_on_run_shell(monkeypatch)
    use_fake(monkeypatch, [tool_turn("run_shell", **DESTRUCTIVE), text_turn("ok")])

    out = fresh_app.invoke(state(), {"configurable": {
        "thread_id": "unattended", "autonomous": True, "trace": []}})

    assert "__interrupt__" not in out
    assert calls == [], "a destructive call must be denied, not run"
    assert out["verdict"] == "done"


# ============================================== numeric arguments from a model

@pytest.mark.parametrize("kwargs,expected", [
    ({"offset": "0", "limit": "500"}, "one"),   # both as JSON strings
    ({"limit": "2"}, "one"),                    # limit alone
    ({"offset": "1", "limit": 2}, "two"),       # mixed; offset 1 skips line one
    ({"limit": None}, "one"),                   # null
    ({"limit": "lots"}, "one"),                 # nonsense - absent, not a crash
])
def test_read_file_tolerates_numeric_arguments_sent_as_strings(
        tmp_workspace, kwargs, expected):
    """A declared schema is a hint, not enforcement.

    Observed live: every read_file call in the first interactive session raised
    TypeError on string offsets, so the agent rewrote a 43-line file it had never
    read, reducing it to one line - then reported success. The tool must survive
    what models actually send.
    """
    (tmp_workspace / "a.py").write_text("one" + chr(10) + "two" + chr(10) + "three")
    assert expected in read_file("a.py", **kwargs)


def test_run_shell_tolerates_a_string_timeout(tmp_workspace):
    assert "exit code: 0" in run_shell("echo hi", timeout="30")


# ================================================ crash and resume (NFR-302)
#
# The existing checkpoint test proves state survives a CLEAN completion. This is
# the harder claim the checkpointer exists for: a process that dies mid-run must
# lose at most one node of work and must never re-execute a completed turn.
#
# A KeyboardInterrupt is used to simulate the death because it is a BaseException:
# `execute` catches Exception, so an ordinary error would be converted into an
# observation and the run would continue. This propagates straight out of invoke(),
# which is what a killed process looks like from the graph's point of view.

def test_a_crash_mid_run_never_re_executes_completed_work(
        fresh_app, tmp_workspace, monkeypatch):
    """NFR-302 / CE-07 across process death rather than an approval pause."""
    ran = []

    def counting_shell(**kw):
        ran.append(kw["command"])
        if kw["command"] == "boom":
            raise KeyboardInterrupt("simulated kill -9")
        return "exit code: 0\n--- stdout ---\n--- stderr ---\n"

    monkeypatch.setitem(TOOLS["run_shell"], "fn", counting_shell)
    use_fake(monkeypatch, [
        tool_turn("run_shell", cid="t1", command="first"),
        tool_turn("run_shell", cid="t2", command="boom"),
        text_turn("Done."),
    ])
    cfg_ = {"configurable": {"thread_id": "crashed", "autonomous": True, "trace": []}}

    with pytest.raises(KeyboardInterrupt):
        fresh_app.invoke(state(), cfg_)

    assert ran == ["first", "boom"], "the run reached the crash"
    checkpointed = fresh_app.get_state(cfg_).values
    assert checkpointed["turns"] == 1, "turn 1 was committed before the crash"

    # Let the retry succeed. The CRASHED node genuinely does re-run on resume -
    # its work never committed, so retrying it is correct and is precisely what
    # "at most one node of work lost" means. The claim under test is narrower and
    # stronger: turn 1, which DID commit, must not run a second time.
    ran_after_crash = len(ran)
    def retry_ok(**kw):
        ran.append(kw["command"])
        return "exit code: 0\n--- stdout ---\n--- stderr ---\n"

    monkeypatch.setitem(TOOLS["run_shell"], "fn", retry_ok)

    # Resume: same thread id, None payload. Re-invocation, not a restart.
    fresh_app.invoke(None, cfg_)

    assert ran[ran_after_crash:] == ["boom"], (
        f"resume re-ran {ran[ran_after_crash:]}; only the uncommitted turn may retry")

    assert ran.count("first") == 1, (
        f"completed work re-executed: {ran}. This is exactly the duplication the "
        f"gate/execute split exists to prevent.")


def test_resume_after_a_crash_preserves_history_and_continues(
        fresh_app, tmp_workspace, monkeypatch):
    """At most one node of work is lost: the crashed turn re-runs, everything
    before it is read from the checkpoint rather than recomputed."""
    def flaky_shell(**kw):
        if kw["command"] == "boom":
            raise KeyboardInterrupt("simulated kill -9")
        return "exit code: 0\n--- stdout ---\n--- stderr ---\n"

    monkeypatch.setitem(TOOLS["run_shell"], "fn", flaky_shell)
    seen = use_fake(monkeypatch, [
        tool_turn("run_shell", cid="t1", command="first"),
        tool_turn("run_shell", cid="t2", command="boom"),
        text_turn("Done."),
    ])
    cfg_ = {"configurable": {"thread_id": "crash-history", "autonomous": True, "trace": []}}

    with pytest.raises(KeyboardInterrupt):
        fresh_app.invoke(state(), cfg_)
    before = len(fresh_app.get_state(cfg_).values["messages"])
    model_calls_before = len(seen)

    monkeypatch.setitem(TOOLS["run_shell"], "fn",
                        lambda **kw: "exit code: 0\n--- stdout ---\n--- stderr ---\n")
    final = fresh_app.invoke(None, cfg_)

    assert final["messages"][0]["content"] == "fix it", "the original goal survived"
    assert len(final["messages"]) > before, "the run continued rather than restarting"
    assert final["turns"] >= 2, "the crashed turn was retried, not skipped"
    # The model is not re-asked for turns it already answered - that is what makes
    # "at most one node of work lost" true rather than "the whole run replayed".
    assert len(seen) - model_calls_before <= 1, (
        f"resume re-called the model {len(seen) - model_calls_before} times; "
        f"completed act nodes must come from the checkpoint")


def test_reading_a_directory_says_what_to_do_instead(tmp_workspace):
    """The only failure in the 14/15 baseline, and it was pure tool ergonomics.

    The agent asked for a directory, got `IsADirectoryError: [Errno 21]` with no
    guidance, retried the identical path with a trailing slash, got the identical
    message back, and burned 3 of its 12 turns learning nothing. The case passes
    in 11 turns and the cap is 12 - so those 3 turns were the whole failure.

    An error the model cannot act on costs a turn every time it is retried.
    """
    (tmp_workspace / "pkg").mkdir()
    with pytest.raises(IsADirectoryError) as caught:
        read_file("pkg")
    message = str(caught.value)
    assert "run_shell" in message and "ls" in message, (
        "the error must name the tool that WOULD work")
    assert "pkg" in message, "and the path it was asked about"


def test_reading_a_missing_file_says_what_DOES_exist(tmp_workspace):
    """The most common tool failure in the whole trace archive: 82 of 112 read_file
    errors are FileNotFoundError, four times the directory case that was already
    fixed.

    A bare `[Errno 2] No such file or directory: '/workspace/stats.py'` tells the
    agent the guess was wrong and nothing about what to guess next, so it guesses
    again. Naming the siblings converts a retry loop into one read.
    """
    (tmp_workspace / "calc.py").write_text("x = 1", encoding="utf-8")
    (tmp_workspace / "helpers.py").write_text("y = 2", encoding="utf-8")
    with pytest.raises(FileNotFoundError) as caught:
        read_file("stats.py")
    message = str(caught.value)
    assert "stats.py" in message, "name what was asked for"
    assert "calc.py" in message and "helpers.py" in message, (
        "and what is actually there - that is the part it can act on")


def test_a_missing_file_in_a_subdirectory_lists_that_subdirectory(tmp_workspace):
    """Sibling listing has to follow the path the agent guessed, or a wrong guess
    deep in a tree gets the workspace root listed at it - noise, not help."""
    (tmp_workspace / "src").mkdir()
    (tmp_workspace / "src" / "parser.py").write_text("x = 1", encoding="utf-8")
    with pytest.raises(FileNotFoundError) as caught:
        read_file("src/parsr.py")
    assert "parser.py" in str(caught.value)


def test_a_missing_file_under_a_missing_directory_does_not_crash(tmp_workspace):
    """The parent may not exist either. Listing it must not raise a SECOND error
    on top of the one being reported."""
    with pytest.raises(FileNotFoundError) as caught:
        read_file("nope/also-nope/file.py")
    assert "file.py" in str(caught.value)
