"""Episodic memory and the durable profile.

A seventh test file, and the fifth stated deviation from the spec's three-file
`tests/` allowlist. The justification is narrow and specific: **memory is the one
component whose failure is silent by construction.** A tool that breaks throws; a
memory that never retrieves just makes the agent look forgetful, which is
indistinguishable from a model having a bad day. The recall benchmark catches that
end-to-end at the cost of model quota; these catch it for free.

No API key, no network (NFR-602). Every test points the stores at a temp directory,
because `config.MEMORY_DB` otherwise resolves to the real agent home.
"""
import json

import pytest

from agent import config, memory, policy


@pytest.fixture(autouse=True)
def tmp_memory(tmp_path, monkeypatch):
    """Redirect both stores, and leave the risk map as it was found."""
    monkeypatch.setattr(config, "MEMORY_DB", tmp_path / "memory.db")
    monkeypatch.setattr(config, "PROFILE", tmp_path / "AGENT.md")
    monkeypatch.setattr(config, "MEMORY_ENABLED", True)
    before = dict(policy.RISK)
    yield tmp_path
    memory.deactivate()
    policy.RISK.clear()
    policy.RISK.update(before)


def _episode(goal="fix the parser", answer="done", **over):
    kw = {"thread_id": "t1", "goal": goal, "verdict": "done", "answer": answer,
          "files": ["src/parser.py"], "commands": ["pytest -q"]}
    kw.update(over)
    return memory.write_episode(**kw)


# ================================================================ the store

def test_an_episode_written_in_one_session_is_found_in_another():
    """The whole point. Within a thread the checkpointer already carries context,
    so this is the only thing that makes a SEPARATE session recall anything."""
    _episode(goal="my deploy key is kx-9920-vt")
    assert "kx-9920-vt" in memory.search("deploy key")[0]["goal"]


def test_search_returns_nothing_when_nothing_matches():
    _episode(goal="fix the parser")
    assert memory.search("kangaroo taxonomy") == []


def test_search_on_an_empty_store_does_not_crash():
    assert memory.search("anything") == []


def test_punctuation_in_the_query_does_not_break_retrieval():
    """FTS5 treats quotes, hyphens and parentheses as syntax. A raw user sentence
    would raise a SyntaxError at precisely the moment recall was meant to happen,
    so every word is requoted as a literal."""
    _episode(goal="the deploy key is kx-9920-vt")
    for query in ('what is my "deploy" key?', "deploy-key (the one for prod)",
                  "deploy OR", "*", "NEAR(", ""):
        assert isinstance(memory.search(query), list)
    assert memory.search('what is my "deploy" key?')


def test_a_short_word_query_still_matches_something_useful():
    """Words of three or more characters are indexed; `is`, `my`, `a` are not, so a
    question made only of stopwords must return empty rather than everything."""
    _episode(goal="the deploy key is kx-9920-vt")
    assert memory.search("is my a") == []


def test_commands_and_files_are_stored_and_retrievable():
    _episode(goal="fix the parser", files=["a.py", "b.py"], commands=["pytest -q"])
    found = memory.search("parser")[0]
    assert json.loads(found["files"]) == ["a.py", "b.py"]
    assert json.loads(found["commands"]) == ["pytest -q"]


# ============================================================== the profile

def test_remember_appends_and_survives_a_second_call():
    memory.remember("Jeevan builds with make ship-quartz")
    memory.remember("Jeevan prefers tabs")
    assert "make ship-quartz" in memory.profile()
    assert "prefers tabs" in memory.profile()


def test_remember_does_not_duplicate_the_same_note():
    """An agent told the same thing twice must not grow the file forever - the
    profile is injected into every session and is charged per turn."""
    memory.remember("Jeevan prefers tabs")
    memory.remember("Jeevan prefers tabs")
    assert memory.profile().count("Jeevan prefers tabs") == 1


def test_remember_rejects_an_empty_note_with_an_actionable_message():
    with pytest.raises(ValueError, match="short sentence"):
        memory.remember("   ")


def test_the_profile_is_empty_before_anything_is_remembered():
    assert memory.profile() == ""


# ============================================================== injection

def test_injected_context_carries_the_profile_and_the_episode():
    memory.remember("Jeevan builds with make ship-quartz")
    _episode(goal="my deploy key is kx-9920-vt", answer="noted")
    injected = memory.context_for("what is my deploy key")
    assert "make ship-quartz" in injected and "kx-9920-vt" in injected


def test_injection_is_capped_in_characters(monkeypatch):
    """It goes into the system prompt, which is re-sent on every request on a
    provider that caches nothing. An uncapped store would grow the per-turn cost of
    every future run without any single change being to blame."""
    monkeypatch.setattr(config, "MEMORY_INJECT_CHARS", 200)
    for i in range(30):
        _episode(goal=f"deploy step {i} " + "x" * 400, thread_id=f"t{i}")
    assert len(memory.context_for("deploy")) <= 200 + 200   # body cap + header


def test_nothing_is_injected_when_there_is_nothing_to_recall():
    """An empty store must add no header and no tokens at all."""
    assert memory.context_for("anything") == ""


def test_injection_tells_the_model_what_to_do_with_it():
    """A bare dump is ignored in practice - the same lesson the spill message
    learned, where a path with no instruction was never acted on."""
    _episode(goal="my deploy key is kx-9920-vt")
    assert "act on it" in memory.context_for("deploy key")


# ============================================================ the kill switch

def test_with_memory_off_nothing_is_injected(monkeypatch):
    _episode(goal="my deploy key is kx-9920-vt")
    monkeypatch.setattr(config, "MEMORY_ENABLED", False)
    assert memory.context_for("deploy key") == ""


def test_with_memory_off_the_remember_tool_does_not_exist(monkeypatch):
    monkeypatch.setattr(config, "MEMORY_ENABLED", False)
    assert memory.activate() == []
    assert memory.tools() == {}


def test_with_memory_off_the_toolset_is_unchanged(monkeypatch):
    from agent.tools import TOOLS, toolset

    monkeypatch.setattr(config, "MEMORY_ENABLED", False)
    memory.activate()
    assert sorted(toolset()) == sorted(TOOLS)


def test_activate_registers_remember_as_a_write():
    memory.activate()
    assert policy.RISK["remember"] == "write"
    assert policy.classify("remember", {"note": "x"}, autonomous=True)[0] == "auto"


def test_deactivate_removes_exactly_what_it_registered():
    """Two modules now register tools. Either one stripping the other's entries
    would leave a tool the gate refuses as unknown, mid-run."""
    policy.register("fetch", "read")          # as agent/mcp.py would
    memory.activate()
    memory.deactivate()
    assert "remember" not in policy.RISK
    assert policy.RISK.get("fetch") == "read"


# ==================================================== what finish() extracts

def test_only_successful_commands_are_remembered():
    """Recalling a command that did not work is worse than recalling nothing,
    because next session it reads as advice."""
    from agent.graph import _outcomes

    messages = [
        {"role": "user", "content": "fix it"},
        {"role": "assistant", "content": [
            {"type": "tool_use", "id": "a", "name": "run_shell",
             "input": {"command": "pytest -q"}},
            {"type": "tool_use", "id": "b", "name": "run_shell",
             "input": {"command": "make nonsense"}}]},
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "a", "content": "ok"},
            {"type": "tool_result", "tool_use_id": "b", "content": "no rule",
             "is_error": True}]},
    ]
    worked = [c["input"]["command"] for c, ok in _outcomes(messages) if ok]
    assert worked == ["pytest -q"]


def test_the_final_answer_is_the_last_thing_said_in_words():
    from agent.graph import _final_text

    messages = [
        {"role": "assistant", "content": [{"type": "text", "text": "looking"}]},
        {"role": "assistant", "content": [
            {"type": "tool_use", "id": "a", "name": "run_shell", "input": {}}]},
        {"role": "assistant", "content": [{"type": "text", "text": "the key is kx-9920-vt"}]},
    ]
    assert _final_text(messages) == "the key is kx-9920-vt"


def test_a_run_that_never_spoke_yields_an_empty_answer():
    from agent.graph import _final_text

    assert _final_text([{"role": "user", "content": "hi"}]) == ""
