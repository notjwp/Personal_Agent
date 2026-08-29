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
from pathlib import Path
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
        "compact_count": 0,
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
    # Was `compact`, which terminated the run and READ as a budget stop while
    # actually being a compaction trigger at 60%. NFR-401 asks for a hard stop
    # and FR-104 names "budget exhausted" as a terminal outcome; both are real
    # now that compaction routes back to act instead of to finish.
    assert final["verdict"] == "budget"
    assert final["spent_tokens"] >= final["budget_tokens"]


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
    # Timing entries are excluded: `node` fires around every node and `checkpoint`
    # after each write, so the last raw entry is a stopwatch reading. What this
    # asserts is that the run ends with a terminal RECORD.
    substantive = [k for k in kinds if k not in ("node", "checkpoint")]
    assert substantive[-1] == "terminal"
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


def test_reading_a_directory_returns_the_listing(tmp_workspace):
    """FR-201's "list directories", answered without a second tool.

    Three versions of this, each paid for. `IsADirectoryError: [Errno 21]` with no
    guidance was the ONLY failure in the 14/15 baseline: the agent retried the
    identical path with a trailing slash, got the identical message, and burned 3
    of its 12 turns. Naming `ls` fixed that and the case passed in 11.

    It still cost a round trip - the planning traces show read_file on a
    directory, the error, then `ls -la` on the same path, two turns for one
    answer. A dedicated list tool would cost ~582 chars of schema on EVERY
    request against a 6,000 cap, to answer a question this tool is already being
    asked. So it just answers.
    """
    (tmp_workspace / "pkg").mkdir()
    (tmp_workspace / "pkg" / "core.py").write_text("x = 1", encoding="utf-8")
    (tmp_workspace / "pkg" / "sub").mkdir()

    out = read_file("pkg")

    assert "is a directory" in out
    assert "core.py" in out, "the listing is the point"
    assert "sub/" in out, "and a directory is marked as one"


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


# ============================================================== continue_state

from agent.graph import continue_state, new_state


def test_continue_state_appends_the_message_and_keeps_the_history():
    prior = {**new_state("fix the failing tests"), "verdict": "done"}
    nxt = continue_state(prior, "now add a CSV exporter")

    assert nxt["messages"][-1] == {"role": "user",
                                   "content": "now add a CSV exporter"}
    assert nxt["messages"][:-1] == prior["messages"]


def test_continue_state_resets_the_turn_counter():
    """reflect (b) fires on turns >= max_turns. Without this reset, a second
    message into a thread that used its allowance returns `stuck` before the
    model is called even once."""
    prior = {**new_state("first"), "turns": 25, "failures": 2, "verdict": "stuck"}
    nxt = continue_state(prior, "second")

    assert nxt["turns"] == 0
    assert nxt["failures"] == 0
    # A verdict left in place routes reflect straight to finish.
    assert nxt["verdict"] is None


def test_continue_state_leaves_the_spend_alone_so_the_budget_still_binds():
    """The omission IS the safety property. Nodes overwrite per key, so every
    field left out keeps its checkpointed value - which makes token spend
    cumulative across a whole conversation rather than resetting per message."""
    prior = {**new_state("first"), "spent_tokens": 190_000, "budget_tokens": 200_000}
    nxt = continue_state(prior, "second")

    assert "spent_tokens" not in nxt
    assert "budget_tokens" not in nxt
    assert "max_turns" not in nxt


def test_continue_state_reenters_a_finished_thread(fresh_app, tmp_workspace,
                                                   monkeypatch):
    """End to end against the real graph, which is the only thing that proves
    this: a thread that already returned a verdict accepts another message and
    runs again, with the first exchange still in front of it.

    Two turns per exchange because reflect only returns `done` once a tool call
    has been made - a text-only reply on its own is `continue`, not termination.
    """
    monkeypatch.setattr(config, "PLAN_ENABLED", False)
    spy_on_run_shell(monkeypatch)
    use_fake(monkeypatch, [tool_turn("run_shell", command="pytest -q"),
                           text_turn("first answer"),
                           tool_turn("run_shell", cid="t2", command="pytest -q"),
                           text_turn("second answer")])
    cfg = {"configurable": {"thread_id": "chat1", "autonomous": True, "trace": []}}

    fresh_app.invoke(new_state("first goal"), cfg)
    prior = fresh_app.get_state(cfg).values
    assert prior["verdict"] == "done", "the first exchange should have ended"

    fresh_app.invoke(continue_state(prior, "second goal"), cfg)
    after = fresh_app.get_state(cfg).values

    assert after["messages"][0]["content"] == "first goal"
    assert any(m.get("content") == "second goal" for m in after["messages"])
    assert "second answer" in str(after["messages"][-1])
    # The turn counter restarted; the spend did not.
    assert after["spent_tokens"] > prior["spent_tokens"]


# ================================================== planning (FR-101, FR-105)
#
# Section 3 draws PLAN as a node that calls a model. It is built as a PHASE
# instead - CE-04, two nodes that never branch apart are one node - so what has
# to be proven here is that the three things which DO differ actually differ:
# the prompt act injects, what the gate refuses, and how reflect exits.

from agent.graph import _steps, act, adopt, reflect


def planning(**over):
    return state(**{"phase": "planning", "plan": [], "cursor": 0,
                    "plan_turns": 0, **over})


def working(plan, cursor=0, **over):
    return state(**{"phase": "working", "plan": plan, "cursor": cursor,
                    "plan_turns": 0, **over})


# --- parsing ---------------------------------------------------------------

def test_steps_parses_numbered_and_bulleted_lines():
    assert _steps("Here is the plan:\n"
                  "1. Read tests/test_export.py\n"
                  "2) Add a CSV writer\n"
                  "- Run pytest -q\n"
                  "* And again\n") == ["Read tests/test_export.py",
                                        "Add a CSV writer",
                                        "Run pytest -q",
                                        "And again"]


def test_steps_truncates_rather_than_refusing():
    """A planner that over-decomposes must not fail the run."""
    many = "\n".join(f"{i}. step number {i}" for i in range(1, 12))
    assert len(_steps(many)) == config.PLAN_MAX_STEPS


def test_steps_returns_nothing_from_prose():
    assert _steps("I will read the file and then fix the bug.") == []


# --- adopt -----------------------------------------------------------------

def test_adopt_falls_back_to_the_goal_when_nothing_parses():
    """A badly phrased plan must never block the run, so the goal itself becomes
    the single step."""
    s = planning(messages=[{"role": "user", "content": "fix the import"},
                           {"role": "assistant",
                            "content": [{"type": "text", "text": "I'll just fix it."}]}])
    out = adopt(s, {"configurable": {"autonomous": True}})
    assert out["plan"] == ["fix the import"]
    assert out["phase"] == "working" and out["cursor"] == 0


def test_adopt_is_silent_in_autonomous_mode():
    """No interrupt at all, or the harness could never run unattended. This is
    the 'one switch apart' the whole phase rests on.

    adopt now APPROVES rather than parses: the plan node wrote it, and the model
    call lives there so a resumed approval does not pay for it twice (CE-07).
    """
    s = planning(plan=["read it", "fix it"])
    out = adopt(s, {"configurable": {"autonomous": True}})
    assert out == {"phase": "working", "plan": ["read it", "fix it"], "cursor": 0}


# --- reflect ---------------------------------------------------------------

def test_reflect_treats_a_text_only_reply_as_the_finished_plan():
    s = planning(messages=[{"role": "user", "content": "fix it"},
                           {"role": "assistant", "content": [
                               {"type": "text", "text": "1. read\n2. fix"}]}])
    assert reflect(s)["verdict"] == "planned"


def test_reaching_the_cap_buys_one_more_turn_to_write_the_plan():
    """Measured, not anticipated. The first version exited straight to adopt at
    the cap, so the last message was a tool result, nothing parsed, and the
    fallback fired on every run - the mechanism never produced a plan at all.
    Reaching the cap now returns to act, which demands one."""
    s = planning(plan_turns=config.PLAN_MAX_TURNS,
                 messages=[{"role": "user", "content": [{"type": "tool_result",
                                                         "tool_use_id": "t1",
                                                         "content": "..."}]}])
    assert reflect(s)["verdict"] == "continue"


def test_a_planner_that_ignores_the_demand_is_still_stopped():
    """The hard stop, one turn past the cap. Reconnaissance must not consume the
    run however determined the planner is."""
    s = planning(plan_turns=config.PLAN_MAX_TURNS + 1,
                 messages=[{"role": "user", "content": [{"type": "tool_result",
                                                         "tool_use_id": "t1",
                                                         "content": "..."}]}])
    assert reflect(s)["verdict"] == "planned"


def test_act_always_offers_tools_now(tmp_workspace, monkeypatch):
    """Two cycles tried to make `act` produce the plan - first by instruction,
    then by withholding the schemas - and the model called a tool both times.
    Neither trick remains; `act` only ever does research or work, and the plan
    node writes the plan with a message list of its own."""
    for s in (planning(plan_turns=0), planning(plan_turns=config.PLAN_MAX_TURNS),
              working(["a"])):
        seen = use_fake(monkeypatch, [text_turn("x")])
        act(s, {"configurable": {}})
        assert seen[0]["tools"], "act always offers tools"
        assert "No research turns left" not in seen[0]["system"]


# --- the plan node, which is what Stage 7 actually is -----------------------

def test_the_plan_node_is_called_with_NO_tool_history(tmp_workspace, monkeypatch):
    """THE point of the stage. Nine scored runs failed because a phase inherits
    the message list, and on this provider a tool-call history keeps producing
    tool calls whether or not a tool is offered - proven with `tools` absent from
    the payload entirely. A fresh list was measured to return text."""
    from agent.graph import plan as plan_node

    seen = use_fake(monkeypatch, [text_turn("1. edit a.py\n2. run the suite")])
    noisy = planning(messages=[
        {"role": "user", "content": "fix a.py"},
        assistant_calls(call("read_file", path="a.py")),
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1",
                                      "content": "x = 1"}]}])

    out = plan_node(noisy, {"configurable": {}})

    sent = seen[0]["messages"]
    assert len(sent) == 1 and sent[0]["role"] == "user", "one user message, no history"
    assert seen[0]["tools"] == [], "and no schemas either"
    assert out["plan"] == ["edit a.py", "run the suite"]


def test_the_plan_node_is_given_a_digest_rather_than_a_transcript(
        tmp_workspace, monkeypatch):
    """Facts, not a conversation. Handing it the transcript would hand it the
    tool-call history, which is the thing being avoided."""
    from agent.graph import plan as plan_node

    seen = use_fake(monkeypatch, [text_turn("1. do it\n2. check it")])
    s = planning(messages=[
        {"role": "user", "content": "fix a.py"},
        assistant_calls(call("read_file", path="tests/test_x.py")),
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1",
                                      "content": "..."}]}])

    plan_node(s, {"configurable": {}})

    sent = seen[0]["messages"][0]["content"]
    assert "fix a.py" in sent, "the goal"
    assert "tests/test_x.py" in sent, "and what was read"
    assert "tool_use" not in sent, "but not the raw blocks"


def test_the_plan_node_falls_back_to_the_goal_rather_than_failing(
        tmp_workspace, monkeypatch):
    """A badly phrased reply must not block the run - the same rule adopt used to
    carry, moved to where the text is now produced."""
    from agent.graph import plan as plan_node

    use_fake(monkeypatch, [text_turn("I will just fix it.")])
    out = plan_node(planning(messages=[{"role": "user", "content": "fix the import"}]),
                    {"configurable": {}})

    assert out["plan"] == ["fix the import"]


def test_a_failed_plan_call_does_not_lose_the_run(tmp_workspace, monkeypatch):
    from agent.graph import plan as plan_node

    monkeypatch.setattr("agent.graph.call_model",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("rate limited")))
    out = plan_node(planning(messages=[{"role": "user", "content": "fix it"}]),
                    {"configurable": {}})

    assert out["plan"] == ["fix it"], "falls back rather than raising"


def test_the_digest_reports_what_worked_and_what_did_not(tmp_workspace):
    from agent.graph import _digest

    messages = [
        {"role": "user", "content": "fix it"},
        assistant_calls(call("read_file", cid="t1", path="core.py"),
                        call("run_shell", cid="t2", command="ls -la")),
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "t1", "content": "ok"},
            {"type": "tool_result", "tool_use_id": "t2", "content": "ok"}]},
        assistant_calls(call("run_shell", cid="t3", command="pytest -q")),
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "t3", "content": "boom",
             "is_error": True}]}]

    digest = _digest(messages)

    assert "core.py" in digest
    assert "ls -la" in digest
    assert "did not work" in digest and "pytest -q" not in digest.split("did not work")[0]


def test_reflect_keeps_researching_below_the_cap():
    s = planning(plan_turns=1,
                 messages=[{"role": "user", "content": [{"type": "tool_result",
                                                         "tool_use_id": "t1",
                                                         "content": "..."}]}])
    assert reflect(s)["verdict"] == "continue"


def test_reflect_advances_the_cursor_instead_of_finishing():
    """Section 9 step 2 (b): the cursor check is restored now that a plan
    exists. Step 1 of 3 completing is progress, not termination."""
    s = working(["a", "b", "c"], cursor=0, messages=[
        {"role": "user", "content": "fix it"},
        {"role": "assistant", "content": [
            {"type": "tool_use", "id": "t1", "name": "read_file", "input": {}}]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1",
                                      "content": "ok"}]},
        {"role": "assistant", "content": [{"type": "text", "text": "step done"}]}])
    out = reflect(s)
    assert out == {"verdict": "continue", "cursor": 1}


def test_reflect_says_done_on_the_last_step():
    s = working(["a", "b"], cursor=1, messages=[
        {"role": "user", "content": "fix it"},
        {"role": "assistant", "content": [
            {"type": "tool_use", "id": "t1", "name": "read_file", "input": {}}]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1",
                                      "content": "ok"}]},
        {"role": "assistant", "content": [{"type": "text", "text": "all done"}]}])
    assert reflect(s)["verdict"] == "done"


def test_the_made_a_call_guard_survives_with_planning_off():
    """With AGENT_PLAN=off the plan is [] and the cursor check would evaluate
    1 >= 0 - the exact false `done` on a first text-only reply that section 9
    step 2 (b) was written to prevent."""
    s = state(messages=[{"role": "user", "content": "fix it"},
                        {"role": "assistant", "content": [
                            {"type": "text", "text": "Let me look at the test first."}]}])
    assert reflect(s)["verdict"] == "continue"


# --- the gate, and the turn counter ----------------------------------------

def test_planning_turns_are_not_charged_against_max_turns(tmp_workspace):
    """The single most important number in this phase. MAX_TURNS is 12; if
    research shared that counter it would starve the cases planning exists to
    help."""
    s = planning(approved=[], denied=[])
    out = execute(s, {"configurable": {}})
    assert out["turns"] == 0, "a research turn must not spend a working turn"
    assert out["plan_turns"] == 1

    out = execute(working(["a"], approved=[], denied=[]), {"configurable": {}})
    assert out["turns"] == 1 and out["plan_turns"] == 0


def test_the_gate_refuses_a_write_while_planning(tmp_workspace):
    """Enforced in the gate, not asked for in the prompt: an approval shown
    after the files have changed is theatre."""
    s = planning(messages=[assistant_calls(call("write_file", path="a.py",
                                                content="x"))])
    out = gate(s, {"configurable": {"autonomous": False}})
    assert out["approved"] == []
    assert "planning" in out["denied"][0]["reason"]


def test_the_gate_allows_reading_while_planning(tmp_workspace):
    s = planning(messages=[assistant_calls(call("read_file", path="a.py"))])
    out = gate(s, {"configurable": {"autonomous": False}})
    assert len(out["approved"]) == 1 and out["denied"] == []


# --- end to end ------------------------------------------------------------

def test_a_run_plans_first_then_works(fresh_app, tmp_workspace, monkeypatch):
    """The whole phase in one pass: it reads, states a plan, adopts it without
    asking (autonomous), and only then is a write approved.

    Turned on explicitly: the default is OFF, because on the measured provider
    the plan is never written - see config.PLAN_ENABLED. The mechanism works
    when the model does reply text-only, which is what this pins.
    """
    monkeypatch.setattr(config, "PLAN_ENABLED", True)
    calls = spy_on_run_shell(monkeypatch)
    seen = use_fake(monkeypatch, [
        tool_turn("read_file", path="a.py"),              # research
        text_turn("finished looking"),                    # research ends
        text_turn("1. edit a.py\n2. run the suite"),      # the PLAN NODE's own call
        tool_turn("run_shell", cid="t2", command="pytest -q"),
        text_turn("step one done"),                       # advances the cursor
        tool_turn("run_shell", cid="t3", command="pytest -q"),
        text_turn("green"),                               # last step -> done
    ])
    cfg = {"configurable": {"thread_id": "plan-e2e", "autonomous": True, "trace": []}}
    (tmp_workspace / "a.py").write_text("x = 1", encoding="utf-8")

    out = fresh_app.invoke(new_state("fix a.py"), cfg)

    assert out["plan"] == ["edit a.py", "run the suite"]
    assert out["phase"] == "working"
    assert out["plan_turns"] == 1, "one research turn, charged to planning"
    assert out["verdict"] == "done"
    # The shell ran only AFTER the plan was adopted. During planning the same
    # command would have been refused - and a text-only reply advances the
    # cursor rather than terminating, which is section 4.1 step 8(e) exactly:
    # "done if final step, else continue + advance cursor".
    assert calls == [{"command": "pytest -q"}] * 2
    # The planning instruction reached the model on the first call and not after.
    assert "You are planning, not working" in seen[0]["system"]
    # seen[2] is the PLAN NODE: its own message list, no history, no schemas.
    assert len(seen[2]["messages"]) == 1 and seen[2]["tools"] == []
    assert "## Your plan" in seen[3]["system"], "and then work resumes with it"


def test_plan_off_restores_the_previous_agent(fresh_app, tmp_workspace, monkeypatch):
    """The kill switch, to the same standard as AGENT_MEMORY and AGENT_SKILLS."""
    monkeypatch.setattr(config, "PLAN_ENABLED", False)
    spy_on_run_shell(monkeypatch)
    seen = use_fake(monkeypatch, [tool_turn("run_shell", command="pytest -q"),
                                  text_turn("green")])
    cfg = {"configurable": {"thread_id": "plan-off", "autonomous": True, "trace": []}}

    out = fresh_app.invoke(new_state("fix it"), cfg)

    assert out["verdict"] == "done"
    assert out["plan"] == [] and out["phase"] == "working"
    assert "You are planning, not working" not in seen[0]["system"]
    assert "## Your plan" not in seen[0]["system"]


# ============================================ Stage 1: the audit closures
#
# Six requirements the audit found unmet or half-met, none of which needed any
# third-party code. Grouped here rather than scattered, because what they have in
# common is that each was reported as satisfied on the strength of code existing.

from agent import policy
from agent.context import redact, shrink
from agent.policy import classify
from agent.tools import run_python


# --- NFR-601: adding a tool touches exactly ONE file -----------------------

def test_a_tool_declared_only_in_tools_py_is_classifiable(tmp_workspace, monkeypatch):
    """The Definition-of-Done item that was false as written.

    `TOOLS` lived in tools.py and `RISK` was a literal in policy.py, so every new
    built-in touched two files. This is the property the requirement actually
    asks for: declare the tool in ONE place and the gate can already classify it.
    """
    from agent.tools import TOOLS

    monkeypatch.setitem(TOOLS, "probe_tool",
                        {"fn": lambda: "ok", "risk": "read", "schema": {}})
    verdict, reason = classify("probe_tool", {}, autonomous=True)

    assert verdict == "auto", reason
    assert policy.risk_of("probe_tool") == "read"


def test_a_tool_declaring_no_risk_is_denied(tmp_workspace, monkeypatch):
    """Fail closed, the same default register() applies to an unclassified MCP
    tool. A missing declaration must not read as `read`."""
    from agent.tools import TOOLS

    monkeypatch.setitem(TOOLS, "undeclared", {"fn": lambda: "ok", "schema": {}})
    verdict, reason = classify("undeclared", {}, autonomous=True)

    assert verdict == "deny"
    assert "unknown tool" in reason


def test_the_builtin_risks_still_match_what_they_always_were(tmp_workspace):
    """The refactor must not have quietly relabelled anything - a `write` that
    became `read` would auto-approve where it used to."""
    assert policy.risk_of("read_file") == "read"
    for name in ("write_file", "edit_file", "run_shell"):
        assert policy.risk_of(name) == "write", name


# --- FR-203: execute Python, including the final expression ----------------

def test_run_python_returns_the_value_of_the_final_expression(tmp_workspace):
    """The half of FR-203 that `run_shell(command="python -c ...")` can never
    satisfy, because -c discards the value."""
    out = run_python("rows = [1, 2, 3]\nsum(rows) * 2")
    assert "--- value ---" in out
    assert "12" in out


def test_run_python_captures_stdout(tmp_workspace):
    out = run_python("print('hello from the tool')")
    assert "hello from the tool" in out
    assert "exit code: 0" in out


def test_run_python_returns_a_traceback_rather_than_raising(tmp_workspace):
    """FR-208: the tool reports, the execute node decides. A traceback IS the
    answer to 'run this', not a failure of the tool."""
    out = run_python("raise ValueError('deliberate')")
    assert "ValueError: deliberate" in out
    assert "Traceback" in out
    assert "exit code: 1" in out


def test_run_python_assignment_alone_yields_no_value(tmp_workspace):
    """A trailing STATEMENT is not an expression. Printing `None` after every
    assignment would be noise in every result."""
    out = run_python("x = 41 + 1")
    assert "--- value ---" not in out


def test_run_python_runs_in_the_workspace(tmp_workspace):
    (tmp_workspace / "data.txt").write_text("payload", encoding="utf-8")
    out = run_python("open('data.txt').read()")
    assert "payload" in out


# --- NFR-203: secrets never enter context ----------------------------------

def test_a_secret_echoed_by_a_tool_is_redacted(tmp_workspace, monkeypatch):
    """The requirement's second half, which did not exist. `run_shell("env")`,
    a traceback carrying a credentialed URL, a config file the agent was asked
    to read - all of them arrive through shrink()."""
    monkeypatch.setenv("NVIDIA_API_KEY", "nvapi-01234567890abcdef")

    out = shrink("run_shell", "AUTH=nvapi-01234567890abcdef\nOK")

    assert "nvapi-01234567890abcdef" not in out
    assert "[redacted:NVIDIA_API_KEY]" in out, (
        "named, not blanked: the model should know a credential was there")


def test_redaction_covers_the_spilled_artifact_too(tmp_workspace, monkeypatch):
    """Redacting only the returned string would leave the secret on disk inside
    the workspace, one read_file away."""
    monkeypatch.setenv("SOME_TOKEN", "tok-0123456789abcdef")
    big = ("AUTH=tok-0123456789abcdef\n" + "filler line\n" * 2000)

    out = shrink("run_shell", big)
    spilled = out.split("[full output: ", 1)[1].split("]", 1)[0]

    assert "tok-0123456789abcdef" not in Path(spilled).read_text(encoding="utf-8")


def test_a_short_env_value_is_not_treated_as_a_secret(tmp_workspace, monkeypatch):
    """A two-character token would rewrite half of any English output."""
    monkeypatch.setenv("TINY_KEY", "ab")
    assert redact("a rabbit sat") == "a rabbit sat"


def test_an_ordinary_env_var_is_left_alone(tmp_workspace, monkeypatch):
    """Only secret-SHAPED names. Redacting PATH would mangle every traceback."""
    monkeypatch.setenv("PROJECT_NAME", "personal-agent")
    assert redact("built personal-agent") == "built personal-agent"


# --- NFR-304: the third cap ------------------------------------------------

def test_running_out_of_wall_clock_terminates(tmp_workspace):
    """Turns and tokens were capped and wall-clock was not, so work that spends
    neither - a shell command inside its own timeout, a provider stalling through
    its retries - had nothing to stop it."""
    s = state(spent_seconds=config.MAX_SECONDS + 1)
    assert reflect(s)["verdict"] == "stuck"


def test_the_wall_clock_cap_is_not_a_fifth_verdict(tmp_workspace):
    """FR-104 names exactly four terminal outcomes. Running out of time is a way
    of being stuck, not a new kind of ending."""
    assert reflect(state(spent_seconds=config.MAX_SECONDS + 1))["verdict"] in (
        "done", "stuck", "compact", "replan")


def test_working_seconds_accumulate_rather_than_reading_the_clock(tmp_workspace):
    """Accumulated by the nodes that spend the time, so a thread resumed a week
    later is not instantly over its cap because the calendar moved."""
    out = execute(state(spent_seconds=12.0, approved=[], denied=[]),
                  {"configurable": {}})
    assert out["spent_seconds"] >= 12.0
    assert out["spent_seconds"] < 12.0 + 5, "one empty node, not a wall clock"


def test_run_python_is_actually_exposed_to_the_model(tmp_workspace):
    """A tool the model cannot see is a function, not a capability.

    Caught by a live check rather than by the suite: the function and its tests
    existed and passed while the schema entry was missing, so the model was never
    offered it. FR-203 is satisfied when the model can CALL it.
    """
    from agent.tools import TOOLS

    assert "run_python" in TOOLS
    entry = TOOLS["run_python"]
    assert entry["risk"] == "write"
    assert entry["schema"]["input_schema"]["required"] == ["code"]


# ================================ Stage 2a: the latency NFRs (NFR-102, NFR-103)
#
# Never measured once - not failed, never attempted. Both are measurable with no
# model and no network, which is why they are tests rather than a script: with a
# stand-in model and a stubbed tool, everything left IS the framework, and a
# latency number nobody re-runs stops being true.

from eval.harness import checkpoint_ms, latency, overheads, percentile


def _cycling(monkeypatch):
    """A model that alternates one tool call, then a text reply.

    Each session is therefore act -> gate -> execute -> reflect -> act ->
    reflect -> finish: seven nodes, one tool, two model calls. Ten sessions give
    a sample large enough for a p95 to mean something.
    """
    step = {"n": 0}

    def cycling(messages, system, tools, on_text=None):
        step["n"] += 1
        return (tool_turn("run_shell", command="echo ok") if step["n"] % 2
                else text_turn("Done."))

    monkeypatch.setattr("agent.graph.call_model", cycling)


def _drive(app, monkeypatch, sessions=10):
    spy_on_run_shell(monkeypatch)
    _cycling(monkeypatch)
    trace = []
    for i in range(sessions):
        app.invoke(state(), {"configurable": {
            "thread_id": f"perf-{i}", "autonomous": True, "trace": trace}})
    return trace


# --- the helper ------------------------------------------------------------

def test_percentile_handles_the_degenerate_samples():
    """An empty sample answers rather than raising: a latency table that crashes
    on a run with no checkpoints is worse than one reporting a count of 0."""
    assert percentile([], 95) == 0.0
    assert percentile([7.0], 95) == 7.0
    assert percentile([0.0, 10.0], 50) == 5.0


def test_latency_reports_the_sample_size_beside_the_figure():
    """count is the load-bearing field. A p95 over four samples is not a p95, and
    printing one without the count invites exactly that reading."""
    stats = latency([1.0, 2.0, 3.0, 4.0])
    assert stats["count"] == 4
    assert stats["p50_ms"] == 2.5 and stats["max_ms"] == 4.0


def test_overheads_subtract_model_and_tool_time():
    """NFR-102's wording exactly: framework cost EXCLUDING model and tool time.
    A node that spent 200ms of its 210ms waiting on the model cost 10."""
    trace = [{"kind": "model", "ms": 200.0},
             {"kind": "node", "node": "act", "ms": 210.0},
             {"kind": "tool", "duration_ms": 40.0},
             {"kind": "tool", "duration_ms": 10.0},
             {"kind": "node", "node": "execute", "ms": 55.0},
             {"kind": "node", "node": "reflect", "ms": 2.0}]
    assert overheads(trace) == [10.0, 5.0, 2.0]


def test_overheads_never_go_negative():
    """Two clocks, read at different moments. A node reading marginally shorter
    than the work inside it is measurement noise, not a negative cost."""
    trace = [{"kind": "model", "ms": 100.0},
             {"kind": "node", "node": "act", "ms": 99.9}]
    assert overheads(trace) == [0.0]


# --- NFR-102 ---------------------------------------------------------------

def test_framework_overhead_per_iteration_is_within_its_cap(
        fresh_app, tmp_workspace, monkeypatch):
    """NFR-102: <= 250 ms per loop iteration, excluding model and tool time."""
    trace = _drive(fresh_app, monkeypatch)
    stats = latency(overheads(trace))

    assert stats["count"] >= 50, f"too small a sample for a p95: {stats}"
    assert stats["p95_ms"] <= 250, f"NFR-102 breached: {stats}"


def test_the_pure_python_nodes_are_effectively_free(
        fresh_app, tmp_workspace, monkeypatch):
    """gate and reflect wait on nothing - no model, no tool, no disk. If either
    is measurable in milliseconds the loop has grown something it should not
    have, and NFR-102's headroom is being spent somewhere invisible."""
    trace = _drive(fresh_app, monkeypatch)
    for node in ("gate", "reflect"):
        stats = latency([e["ms"] for e in trace
                         if e.get("kind") == "node" and e.get("node") == node])
        assert stats["count"] >= 10, node
        assert stats["p95_ms"] <= 25, f"{node} got expensive: {stats}"


# --- NFR-103 ---------------------------------------------------------------

def test_checkpoint_writes_are_within_their_cap(
        fresh_app, tmp_workspace, monkeypatch):
    """NFR-103: <= 50 ms at p95. State is written after every node transition,
    so this is paid more often than any other cost in the system."""
    trace = _drive(fresh_app, monkeypatch)
    stats = latency(checkpoint_ms(trace))

    assert stats["count"] >= 50, f"too small a sample for a p95: {stats}"
    assert stats["p95_ms"] <= 50, f"NFR-103 breached: {stats}"


def test_the_timed_saver_still_round_trips_state(fresh_app, tmp_workspace, monkeypatch):
    """The one measurement that reaches into a dependency's surface. A subclass
    that timed writes but dropped one would lose the checkpointer's whole point,
    and every latency figure would look excellent."""
    spy_on_run_shell(monkeypatch)
    use_fake(monkeypatch, [tool_turn("run_shell", command="echo ok"),
                           text_turn("Done.")])
    cfg = {"configurable": {"thread_id": "saver-1", "autonomous": True, "trace": []}}

    fresh_app.invoke(state(), cfg)
    restored = fresh_app.get_state(cfg).values

    assert restored["verdict"] == "done"
    assert restored["turns"] == 1
    assert restored["messages"][0]["content"] == "fix it"


# ================================================ FR-206: search_files
#
# "Repository inspection that returns paths and line numbers, NOT file contents."
# The last clause is the requirement, and it is why run_shell with grep does not
# satisfy it: grep returns every matching line unbounded, which is the context
# flood shrink() exists to contain.

from agent.tools import LINE_CHARS, MATCH_CAP, search_files


def _tree(root):
    (root / "pkg").mkdir()
    (root / "pkg" / "core.py").write_text(
        "import os\n\n\ndef parse_date(value):\n    return value\n", encoding="utf-8")
    (root / "pkg" / "util.py").write_text(
        "def parse_date(value):\n    pass\n", encoding="utf-8")
    (root / "notes.md").write_text("parse_date is the culprit\n", encoding="utf-8")


def test_a_match_reports_path_and_line_number(tmp_workspace):
    _tree(tmp_workspace)
    out = search_files("def parse_date")

    assert "pkg/core.py:4: def parse_date(value):" in out
    assert "pkg/util.py:1: def parse_date(value):" in out


def test_a_glob_narrows_the_search(tmp_workspace):
    _tree(tmp_workspace)
    out = search_files("parse_date", glob="**/*.md")

    assert "notes.md" in out
    assert "core.py" not in out


def test_paths_only_drops_the_lines_and_collapses_repeats(tmp_workspace):
    """FR-206's "paths, not contents" in its strictest form. A file with forty
    matches must appear once, or the mode is just a differently-shaped flood."""
    _tree(tmp_workspace)
    (tmp_workspace / "many.py").write_text("x\n" * 5 + "hit\n" * 40, encoding="utf-8")

    out = search_files("hit", paths_only=True)

    assert out.strip() == "many.py"
    assert ":" not in out, "paths_only must not carry line numbers or text"


def test_the_cap_is_enforced_AND_announced(tmp_workspace):
    """A silent truncation reads as 'that is all there is' and the agent stops
    looking. Saying how many were withheld is the difference between a bound and
    a lie."""
    (tmp_workspace / "big.py").write_text("needle\n" * 200, encoding="utf-8")

    out = search_files("needle")

    assert len([l for l in out.splitlines() if l.startswith("big.py:")]) == MATCH_CAP
    assert f"{MATCH_CAP} of 200 matches shown" in out


def test_a_long_line_is_truncated(tmp_workspace):
    """Enough to judge relevance, not enough to be a way of reading a file."""
    (tmp_workspace / "wide.py").write_text("needle " + "x" * 5000, encoding="utf-8")

    out = search_files("needle")

    assert len(out.splitlines()[0]) < LINE_CHARS + 40
    assert "x" * 200 not in out


def test_no_match_says_so_and_says_where_it_looked(tmp_workspace):
    """An empty string reads as a broken tool. Naming the count and the glob lets
    the agent tell 'wrong pattern' from 'wrong place'."""
    _tree(tmp_workspace)
    out = search_files("zzz_absent")

    assert "no match" in out
    assert "file(s)" in out


def test_a_bad_regex_explains_itself(tmp_workspace):
    """FR-208: the tool raises, execute turns it into an observation. What it must
    not do is return zero matches, which looks like a correct empty answer."""
    _tree(tmp_workspace)
    with pytest.raises(ValueError) as caught:
        search_files("[unclosed")
    assert "regular expression" in str(caught.value)


def test_noise_directories_are_skipped(tmp_workspace):
    """.git alone is thousands of files and would consume the cap before any
    source was reached."""
    (tmp_workspace / ".git").mkdir()
    (tmp_workspace / ".git" / "COMMIT_EDITMSG").write_text("needle", encoding="utf-8")
    (tmp_workspace / "__pycache__").mkdir()
    (tmp_workspace / "__pycache__" / "x.py").write_text("needle", encoding="utf-8")
    (tmp_workspace / "real.py").write_text("needle", encoding="utf-8")

    out = search_files("needle")

    assert "real.py" in out
    assert ".git" not in out and "__pycache__" not in out


def test_the_search_cannot_escape_the_workspace(tmp_workspace):
    """FR-302, and this test earned its place immediately.

    The first version of search_files claimed to be bounded "by construction"
    because the walk starts at config.WORKSPACE. It was not: `Path.glob("../*")`
    walks straight out, and this test returned a file from the parent directory.
    An escaping glob is now REFUSED rather than silently missed - "no match"
    would read as "nothing is there", which is the wrong lesson for the agent.
    """
    outside = tmp_workspace.parent / "outside_secret.py"
    outside.write_text("needle_outside", encoding="utf-8")
    try:
        assert "no match" in search_files("needle_outside")
        for escape in ("../*", "../../**/*", "/**/*", "**/../*"):
            with pytest.raises(ValueError) as caught:
                search_files("needle_outside", glob=escape)
            assert "outside the workspace" in str(caught.value), escape
    finally:
        outside.unlink()


def test_a_symlink_pointing_out_of_the_workspace_is_dropped(tmp_workspace):
    """The half a pattern check cannot catch. `**/*` is a legal glob; the file it
    reaches through a symlink is not, and only the RESOLVED path knows."""
    outside = tmp_workspace.parent / "outside_linked.py"
    outside.write_text("needle_linked", encoding="utf-8")
    try:
        (tmp_workspace / "link.py").symlink_to(outside)
    except (OSError, NotImplementedError):
        outside.unlink()
        pytest.skip("symlinks not permitted here")
    try:
        assert "no match" in search_files("needle_linked")
    finally:
        outside.unlink()


def test_search_files_is_exposed_and_read_only(tmp_workspace):
    """A tool the model cannot see is a function, not a capability - and this one
    only reads, so it must not pause for approval."""
    from agent.tools import TOOLS

    assert "search_files" in TOOLS
    assert TOOLS["search_files"]["risk"] == "read"
    assert classify("search_files", {"pattern": "x"}, autonomous=True)[0] == "auto"


# ================================================ FR-205: git in the sandbox
#
# "Perform git status, diff, branch, add, commit, push."
#
# A TEST rather than a scored case, deliberately. A case cannot force the agent
# to reach for git - it could read the failing test and fix the code without ever
# running `git log` - and this project has already measured what happens when a
# requirement rests on the model electing to do something (Phase O: `learn`
# called 0 times in 15 sessions). What a case would prove is that the agent
# CHOOSES git; what FR-205 asks is that git WORKS. This proves the second, which
# is the part that was never checked.

def _git(workspace, command):
    """Run one git command in the workspace the way the agent would - through
    run_shell, so the test exercises the real path rather than subprocess."""
    import agent.tools as tools
    return tools.run_shell(command)


def test_git_status_diff_and_log_work_in_the_sandbox(tmp_workspace):
    (tmp_workspace / "a.py").write_text("x = 1\n", encoding="utf-8")

    assert "exit code: 0" in _git(tmp_workspace, "git init -q")
    assert "exit code: 0" in _git(tmp_workspace, "git add -A")

    out = _git(tmp_workspace, "git commit -q -m 'first'")
    assert "exit code: 0" in out, f"commit failed - is git identity set in the image? {out}"

    (tmp_workspace / "a.py").write_text("x = 2\n", encoding="utf-8")

    assert "a.py" in _git(tmp_workspace, "git status --short")
    diff = _git(tmp_workspace, "git diff")
    assert "-x = 1" in diff and "+x = 2" in diff
    assert "first" in _git(tmp_workspace, "git log --oneline")


def test_git_branch_works(tmp_workspace):
    (tmp_workspace / "a.py").write_text("x = 1\n", encoding="utf-8")
    _git(tmp_workspace, "git init -q && git add -A && git commit -q -m first")

    assert "exit code: 0" in _git(tmp_workspace, "git branch feature")
    assert "feature" in _git(tmp_workspace, "git branch --list")


def test_git_commit_has_an_identity(tmp_workspace):
    """The two lines the image was missing. Without them `git commit` fails with
    "Please tell me who you are", which reads as the agent doing something wrong
    rather than the environment being incomplete."""
    out = _git(tmp_workspace, "git config user.email")
    assert "exit code: 0" in out and "@" in out


# FR-205's `push` is NOT tested and cannot be, by design: a scored run has no
# route off the machine except the model allowlist (NFR-205). It is asserted,
# not demonstrated, and CONTEXT.md says so rather than implying otherwise.


# ================================================ FR-207: the @tool decorator
#
# "Register a new tool by decorating one function; derive its JSON schema
# automatically from the signature and docstring."
#
# THE EXPECTED VALUES BELOW ARE THE HAND-WRITTEN SCHEMAS, captured from TOOLS
# immediately BEFORE the conversion. That is the whole safety of this change: the
# schemas are what the model actually sees, so a decorator that quietly reworded
# one would look like a model regression and be diagnosed for days. Anything that
# changes them has to change this literal too, deliberately.

HAND_WRITTEN_SCHEMAS = {'read_file': {'name': 'read_file',
               'description': 'Read a text file from the workspace. Returns numbered '
                              'lines. Use offset and limit to page through a large '
                              'file.',
               'input_schema': {'type': 'object',
                                'properties': {'path': {'type': 'string',
                                                        'description': 'Path relative '
                                                                       'to the '
                                                                       'workspace '
                                                                       'root.'},
                                               'offset': {'type': 'integer',
                                                          'description': 'First line '
                                                                         'to return, '
                                                                         '0-based. '
                                                                         'Default 0.'},
                                               'limit': {'type': 'integer',
                                                         'description': 'How many '
                                                                        'lines to '
                                                                        'return. '
                                                                        'Default '
                                                                        '500.'}},
                                'required': ['path']}},
 'search_files': {'name': 'search_files',
                  'description': 'Find where something appears in the workspace. '
                                 'Returns path:line: matches, never whole files. **Use '
                                 'this instead of run_shell with grep, find or ls** - '
                                 'it is bounded, so it cannot flood your context the '
                                 'way a raw grep across a large repository will. Use '
                                 'read_file once this has told you which file and '
                                 'which line to look at.',
                  'input_schema': {'type': 'object',
                                   'properties': {'pattern': {'type': 'string',
                                                              'description': 'Regular '
                                                                             'expression '
                                                                             'to '
                                                                             'search '
                                                                             'for.'},
                                                  'glob': {'type': 'string',
                                                           'description': 'Which files '
                                                                          'to search, '
                                                                          'e.g. '
                                                                          "'**/*.py'. "
                                                                          'Default all '
                                                                          'files.'},
                                                  'paths_only': {'type': 'boolean',
                                                                 'description': 'Return '
                                                                                'only '
                                                                                'the '
                                                                                'file '
                                                                                'paths, '
                                                                                'one '
                                                                                'per '
                                                                                'file, '
                                                                                'without '
                                                                                'the '
                                                                                'matching '
                                                                                'lines. '
                                                                                'Default '
                                                                                'false.'}},
                                   'required': ['pattern']}},
 'write_file': {'name': 'write_file',
                'description': 'Write a file in the workspace, replacing its entire '
                               'contents. Read the file first; this does not patch, it '
                               'overwrites.',
                'input_schema': {'type': 'object',
                                 'properties': {'path': {'type': 'string',
                                                         'description': 'Path relative '
                                                                        'to the '
                                                                        'workspace '
                                                                        'root.'},
                                                'content': {'type': 'string',
                                                            'description': 'The '
                                                                           'complete '
                                                                           'new '
                                                                           'contents '
                                                                           'of the '
                                                                           'file.'}},
                                 'required': ['path', 'content']}},
 'edit_file': {'name': 'edit_file',
               'description': 'Replace an exact snippet of a file with new text. '
                              'Prefer this over write_file for any change to an '
                              'existing file: it costs a few hundred characters '
                              'instead of the whole file. The snippet must appear '
                              'exactly once - include surrounding lines to make it '
                              'unique.',
               'input_schema': {'type': 'object',
                                'properties': {'path': {'type': 'string',
                                                        'description': 'Path relative '
                                                                       'to the '
                                                                       'workspace '
                                                                       'root.'},
                                               'old_string': {'type': 'string',
                                                              'description': 'The '
                                                                             'exact '
                                                                             'text to '
                                                                             'replace, '
                                                                             'copied '
                                                                             'from the '
                                                                             'file '
                                                                             'including '
                                                                             'indentation.'},
                                               'new_string': {'type': 'string',
                                                              'description': 'The text '
                                                                             'to put '
                                                                             'in its '
                                                                             'place.'}},
                                'required': ['path', 'old_string', 'new_string']}},
 'run_python': {'name': 'run_python',
                'description': 'Run Python in the workspace. Returns stdout, the '
                               'traceback if it raised, and the VALUE of the final '
                               'expression - so end with a bare expression to see what '
                               'it evaluates to, as in a REPL. Prefer this over '
                               'run_shell for anything that computes: `python -c` '
                               'throws the value away.',
                'input_schema': {'type': 'object',
                                 'properties': {'code': {'type': 'string',
                                                         'description': 'Python '
                                                                        'source. The '
                                                                        'last line may '
                                                                        'be a bare '
                                                                        'expression to '
                                                                        'return its '
                                                                        'value.'},
                                                'timeout': {'type': 'integer',
                                                            'description': 'Seconds '
                                                                           'before it '
                                                                           'is killed. '
                                                                           'Default '
                                                                           '120.'}},
                                 'required': ['code']}},
 'run_shell': {'name': 'run_shell',
               'description': 'Run a shell command in the workspace. Returns the exit '
                              'code, stdout and stderr separately. Use this to run '
                              'tests.',
               'input_schema': {'type': 'object',
                                'properties': {'command': {'type': 'string',
                                                           'description': 'The command '
                                                                          'to run.'},
                                               'timeout': {'type': 'integer',
                                                           'description': 'Seconds '
                                                                          'before the '
                                                                          'command is '
                                                                          'killed. '
                                                                          'Default '
                                                                          '120.'}},
                                'required': ['command']}}}


def test_the_generated_schemas_are_byte_identical_to_the_hand_written_ones():
    """The equivalence that made the conversion safe to make at all."""
    from agent.tools import TOOLS

    generated = {name: entry["schema"] for name, entry in TOOLS.items()}
    assert set(generated) == set(HAND_WRITTEN_SCHEMAS)
    for name, expected in HAND_WRITTEN_SCHEMAS.items():
        assert generated[name] == expected, f"{name} drifted from its hand-written schema"


def test_a_description_containing_a_colon_is_not_mistaken_for_a_parameter():
    """search_files' own description says "Returns path:line: matches". A naive
    split on the first colon would eat it as a parameter line and truncate the
    text the model reads."""
    from agent.tools import TOOLS

    described = TOOLS["search_files"]["schema"]["description"]
    assert "path:line: matches" in described
    assert described.endswith("which line to look at.")


def test_required_comes_from_parameters_without_defaults():
    from agent.tools import TOOLS

    assert TOOLS["read_file"]["schema"]["input_schema"]["required"] == ["path"]
    assert TOOLS["edit_file"]["schema"]["input_schema"]["required"] == [
        "path", "old_string", "new_string"]


def test_types_come_from_the_annotations():
    from agent.tools import TOOLS

    props = TOOLS["search_files"]["schema"]["input_schema"]["properties"]
    assert props["pattern"]["type"] == "string"
    assert props["paths_only"]["type"] == "boolean"
    assert TOOLS["read_file"]["schema"]["input_schema"]["properties"]["offset"]["type"] == "integer"


def test_the_decorator_still_declares_risk_in_one_file():
    """NFR-601 must survive FR-207: risk is the one thing a signature cannot
    express, so it stays an explicit argument beside the function."""
    from agent.tools import TOOLS

    assert TOOLS["read_file"]["risk"] == "read"
    assert TOOLS["search_files"]["risk"] == "read"
    for name in ("write_file", "edit_file", "run_shell", "run_python"):
        assert TOOLS[name]["risk"] == "write", name


# ================================== compaction (FR-403, FR-404, NFR-403)

from agent.context import (HEAD_MESSAGES, SUMMARY_PREFIX, TAIL_MESSAGES,
                           boundaries, compact_messages, context_chars, pairs_ok)


def _history(turns):
    """A realistic list: goal, then alternating tool_use / tool_result."""
    out = [{"role": "user", "content": "fix it"}]
    for i in range(turns):
        out.append({"role": "assistant", "content": [
            {"type": "tool_use", "id": f"t{i}", "name": "read_file",
             "input": {"path": f"f{i}.py"}}]})
        out.append({"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": f"t{i}",
             "content": f"contents of f{i}.py " + "x" * 2_000}]})
    return out


# --- the invariant ---------------------------------------------------------

def test_pairs_ok_accepts_a_well_formed_history():
    assert pairs_ok(_history(5))


def test_pairs_ok_catches_an_orphaned_call_and_an_orphaned_result():
    """Both shapes a provider rejects: a tool_use with no answer, and an answer
    with no call."""
    history = _history(3)
    assert not pairs_ok(history[:2])          # keeps a call, drops its result
    assert not pairs_ok(history[2:])          # keeps a result, drops its call


# --- the boundary, which is the whole point --------------------------------

def test_the_naive_boundary_would_split_a_pair_and_the_snap_prevents_it():
    """§4.3 says "the first two messages". Message 1 is a tool_use and message 2
    is its result, so the literal reading orphans the call - measured at 100% of
    466 recorded traces before this existed."""
    history = _history(8)
    assert not pairs_ok(history[:HEAD_MESSAGES]), "the literal §4.3 head is invalid"

    head_end, tail_start = boundaries(history)
    assert head_end > HEAD_MESSAGES, "the head boundary must snap forward"
    assert pairs_ok(history[:head_end])
    assert pairs_ok(history[tail_start:])


def test_compaction_over_every_recorded_trace_keeps_the_list_valid():
    """THE regression this stage exists to prevent, and the strongest offline
    evidence available: every trace this project ever recorded, compacted, and
    checked. It costs a second of CPU and it is what caught the boundary bug."""
    import json as _json

    root = Path(__file__).resolve().parent.parent / "eval" / "runs"
    checked = 0
    for f in root.rglob("*.json"):
        if "manifest" in f.name:
            continue
        try:
            messages = _json.loads(f.read_text(encoding="utf-8")).get("messages") or []
        except (OSError, ValueError):
            continue
        if len(messages) <= HEAD_MESSAGES + TAIL_MESSAGES or not pairs_ok(messages):
            continue
        assert pairs_ok(compact_messages(messages, "did things")), f.name
        checked += 1
    assert checked > 100, f"only {checked} traces available; the corpus is the point"


# --- what is retained ------------------------------------------------------

def test_the_goal_and_the_recent_turns_survive(tmp_workspace):
    """FR-404: opening messages and the most recent turns, verbatim."""
    history = _history(10)
    out = compact_messages(history, "summary text")

    assert out[0] == history[0], "the goal must survive"
    assert out[-TAIL_MESSAGES:] == history[-TAIL_MESSAGES:], "recent turns verbatim"


def test_the_summary_appears_exactly_once():
    out = compact_messages(_history(10), "summary text")
    found = [b for m in out if isinstance(m.get("content"), list)
             for b in m["content"]
             if b.get("type") == "text" and SUMMARY_PREFIX in b.get("text", "")]
    assert len(found) == 1
    assert "summary text" in found[0]["text"]


def test_a_short_history_is_returned_unchanged():
    short = _history(2)
    assert compact_messages(short, "x") == short


def test_compaction_does_not_leave_two_user_messages_adjacent():
    """The summary is appended to the trailing head message rather than inserted
    as its own. Consecutive same-role messages are a shape some providers reject,
    and the head always ends on a user tool_result once the boundary is snapped."""
    out = compact_messages(_history(10), "summary text")
    roles = [m["role"] for m in out]
    assert not any(a == b == "user" for a, b in zip(roles, roles[1:]))


# --- NFR-403 ---------------------------------------------------------------

def test_compaction_halves_the_context_when_it_fires():
    """NFR-403: at least 50% when it fires. Asserted on a history large enough to
    TRIGGER, which is the population the requirement is about - a six-message
    history has nothing to remove and shrinking it is not what was promised."""
    history = _history(40)
    assert context_chars(history) > config.COMPACT_AT_CHARS, "must be over the trigger"

    reduction = 1 - context_chars(compact_messages(history, "short summary")) \
        / context_chars(history)
    assert reduction >= 0.50, f"only {reduction:.0%} removed"


# --- the node, end to end --------------------------------------------------

def test_compaction_returns_to_act_instead_of_ending_the_run(
        fresh_app, tmp_workspace, monkeypatch):
    """The single behavioural change: `compact` used to route to finish, so the
    verdict meant "give up expensively"."""
    spy_on_run_shell(monkeypatch)
    huge = "y" * 60_000
    monkeypatch.setitem(TOOLS["run_shell"], "fn", lambda **kw: huge)

    # shrink() caps each result at MAX_RESULT_CHARS, so context grows ~6k a turn
    # and crossing 45,000 takes several. The fake never stops calling; the run
    # ends at the turn cap, which is fine - what is being proven is that
    # compaction happened along the way instead of ending it.
    step = {"n": 0}

    def cycling(messages, system, tools, on_text=None):
        step["n"] += 1
        # A DIFFERENT command each turn, or reflect's thrash detector (three
        # identical signatures in a row) ends the run at turn 3 - long before
        # context has grown enough to compact. That is the detector working.
        return tool_turn("run_shell", cid=f"t{step['n']}", command=f"cat big{step['n']}")

    monkeypatch.setattr("agent.graph.call_model", cycling)
    trace = []
    out = fresh_app.invoke(state(), {"configurable": {
        "thread_id": "compact-e2e", "autonomous": True, "trace": trace}})

    assert out["compact_count"] >= 1, "compaction never fired"
    assert out["verdict"] != "compact", "compact must no longer be terminal"
    entry = next(e for e in trace if e.get("kind") == "compact")
    assert entry["after"] < entry["before"]
    assert entry["removed_pct"] > 0


def test_a_failed_summariser_does_not_lose_the_run(
        fresh_app, tmp_workspace, monkeypatch):
    """The run is already in trouble - that is why it is compacting. Dying in the
    recovery is worse than losing the detail."""
    from agent.context import compact_messages as _cm
    from agent.graph import compact

    monkeypatch.setattr("agent.graph.call_model",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("rate limited")))
    s = state(messages=_history(10))

    out = compact(s, {"configurable": {"trace": []}})

    assert out["compact_count"] == 1
    text = json.dumps(out["messages"])
    assert "unavailable" in text and "RuntimeError" in text
    assert pairs_ok(out["messages"])


def test_a_missing_prompt_file_fails_loudly(tmp_workspace, monkeypatch):
    """The bug this test exists for was live for one run: prompts/STEPS.md did
    not exist, the broad except swallowed the FileNotFoundError, and the node
    fell back to the goal on every call - looking exactly like the failure Stage
    7 was built to fix. A missing prompt is a deploy error, not a provider one.
    """
    import agent.graph as g
    from pathlib import Path

    monkeypatch.setattr(g, "STEPS", Path("/nonexistent/STEPS.md"))
    use_fake(monkeypatch, [text_turn("1. do it\n2. check it")])

    with pytest.raises(OSError):
        g.plan(planning(messages=[{"role": "user", "content": "fix it"}]),
               {"configurable": {}})
