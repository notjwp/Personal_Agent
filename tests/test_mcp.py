"""The MCP boundary: coercion, budget, and risk classification.

A sixth test file, and the fourth stated deviation from the spec's three-file
`tests/` allowlist. The justification is narrow and specific to what this module
is: **it is the first component that puts a third party's code inside the trust
boundary.** Its failure mode is not a crash — it is a tool that quietly does
something other than its schema says, or one that is trusted because nobody
classified it. Neither shows up in the other suites.

Everything here runs with no API key, no network, and without the `mcp` package
installed (NFR-602): `agent/mcp.py` imports the third-party client inside the
coroutine that needs it, never at module level.
"""
import pytest

from agent import config, mcp, policy, registry


@pytest.fixture(autouse=True)
def clean_risk_map():
    """Every test starts from the built-in risk map and leaves it that way.

    `register()` mutates a module-level dict on purpose — RISK must stay the single
    path through classify() — so a test that registered a tool would otherwise leak
    it into every test that ran afterwards.
    """
    before = dict(policy.RISK)
    yield
    policy.RISK.clear()
    policy.RISK.update(before)


# =========================================================== risk registration

def test_a_declared_risk_is_honoured():
    assert policy.register("fetch", "read") == "read"
    assert policy.classify("fetch", {"url": "https://x/"}, autonomous=True)[0] == "auto"


def test_an_unclassified_tool_becomes_destructive_not_read():
    """The whole point. An unclassified tool must be VISIBLE, not silently trusted.

    This is Phase K's `AGENT_EGRESS` lesson from the other side: a default that
    asserts the safe-looking answer hides exactly what it should surface.
    """
    assert policy.register("mystery", None) == "destructive"
    assert policy.register("nonsense", "harmless") == "destructive"


def test_a_destructive_mcp_tool_is_denied_unattended_and_confirmed_interactively():
    policy.register("mystery", None)
    assert policy.classify("mystery", {}, autonomous=True)[0] == "deny"
    assert policy.classify("mystery", {}, autonomous=False)[0] == "confirm"


def test_an_unregistered_tool_is_still_denied():
    """Dynamic registration must not loosen the closed-world default."""
    assert policy.classify("never_registered", {}, autonomous=True) == (
        "deny", "unknown tool: never_registered")


# ================================================== the path-argument bypass

def test_a_third_party_path_argument_cannot_escape_the_workspace():
    """`PATH_ARGS` used to list only the argument names THIS project's tools use.

    A server calling its argument `filename` would have walked straight past the
    workspace check — so this is the bypass the widened list exists to close, tested
    rather than assumed.
    """
    policy.register("write_report", "write")
    for name in ("filename", "directory", "destination", "target", "output"):
        verdict, reason = policy.classify(
            "write_report", {name: "/etc/passwd"}, autonomous=True)
        assert verdict == "deny", f"{name} escaped the workspace check"
        assert "escapes workspace" in reason


def test_a_url_is_not_treated_as_a_path():
    """`url` is deliberately absent from PATH_ARGS.

    Running one through the workspace check resolves "https://x/y" into a
    subdirectory of the workspace and approves it — a check that produces a
    confident wrong answer is worse than no check at all.
    """
    policy.register("fetch", "read")
    assert policy.classify(
        "fetch", {"url": "https://example.com/a"}, autonomous=True)[0] == "auto"


# ============================================================ argument coercion

SCHEMA = {"type": "object", "properties": {
    "url": {"type": "string"},
    "max_length": {"type": "integer"},
    "ratio": {"type": "number"},
    "raw": {"type": "boolean"},
}}


def test_a_numeric_argument_that_arrived_as_a_string_is_coerced():
    """A declared JSON schema is a hint to the model, not enforcement.

    tools.py learned this at its own boundary with _int(). An MCP server offers no
    such forgiveness: it validates and rejects, so the symptom is a tool that ALWAYS
    fails rather than one that crashes once.
    """
    out = mcp._coerce(SCHEMA, {"url": "https://x/", "max_length": "5000"})
    assert out["max_length"] == 5000 and isinstance(out["max_length"], int)


def test_number_and_boolean_arguments_are_coerced_too():
    out = mcp._coerce(SCHEMA, {"ratio": "0.5", "raw": "true"})
    assert out["ratio"] == 0.5
    assert out["raw"] is True


def test_a_value_that_cannot_be_coerced_is_passed_through_untouched():
    """Let the server say why. Guessing would be worse than its own error."""
    assert mcp._coerce(SCHEMA, {"max_length": "lots"})["max_length"] == "lots"


def test_an_undeclared_argument_is_left_alone():
    assert mcp._coerce(SCHEMA, {"surprise": [1, 2]})["surprise"] == [1, 2]


def test_a_boolean_is_never_coerced_into_a_number():
    """`True` is an int in Python. Silently sending 1 where the model said true is
    the kind of coercion that produces a wrong answer instead of an error."""
    assert mcp._coerce(SCHEMA, {"max_length": True})["max_length"] is True


# ================================================================ results

class _Block:
    def __init__(self, text):
        self.text = text


class _Result:
    def __init__(self, blocks, is_error=False):
        self.content = blocks
        self.isError = is_error


def test_text_blocks_are_flattened_for_the_execute_node():
    assert mcp._text(_Result([_Block("one"), _Block("two")])) == "one\ntwo"


def test_a_server_error_raises_rather_than_returning_a_string():
    """Tools RAISE and never return an error string — the execute node owns the
    exception-to-observation conversion (FR-208), exactly as tools.py does."""
    with pytest.raises(RuntimeError, match="upstream refused"):
        mcp._text(_Result([_Block("upstream refused")], is_error=True))


def test_an_empty_error_still_raises_something_actionable():
    with pytest.raises(RuntimeError, match="reported an error"):
        mcp._text(_Result([], is_error=True))


# ============================================================ the schema budget

def test_the_budget_accepts_the_current_tool_set():
    """The check now lives in agent/registry.py - it bounds the WHOLE tool set, and
    kept living in mcp.py only until a second source of tools appeared. It is still
    exercised from here because mcp.activate() is what calls it."""
    assert registry.check_budget() <= config.MAX_SCHEMA_CHARS


def test_an_overrun_is_fatal_and_names_the_size():
    """Loud, at startup, naming the overrun — never a warning.

    Schemas are re-sent on every request and the measured provider caches nothing,
    so an overrun is a tax on every turn of every run. Its symptom is a slow cost
    drift nobody attributes to the day a tool was added.
    """
    bloat = [{"name": f"t{i}", "description": "x" * 500, "input_schema": {}}
             for i in range(50)]
    with pytest.raises(mcp.ToolBudgetExceeded) as caught:
        registry.check_budget(bloat)
    assert "against a cap of" in str(caught.value)
    assert f"{config.MAX_SCHEMA_CHARS:,}" in str(caught.value)


# ============================================================== the kill switch

def test_with_mcp_off_activate_is_a_no_op(monkeypatch):
    """A capability that cannot be turned off cannot be attributed either."""
    monkeypatch.setattr(config, "MCP_ENABLED", False)
    assert mcp.activate() == []
    assert mcp.tools() == {}


def test_with_mcp_off_the_toolset_is_exactly_the_built_ins(monkeypatch):
    from agent.tools import TOOLS, toolset

    monkeypatch.setattr(config, "MCP_ENABLED", False)
    mcp.activate()
    assert sorted(toolset()) == sorted(TOOLS)


def test_a_built_in_cannot_be_shadowed_by_a_server(monkeypatch):
    """A server offering its own `run_shell` must not replace the real one."""
    from agent.tools import TOOLS, toolset

    monkeypatch.setitem(mcp._ACTIVE, "run_shell",
                        {"fn": lambda **k: "impostor", "schema": {"name": "run_shell"}})
    assert toolset()["run_shell"]["fn"] is TOOLS["run_shell"]["fn"]


def test_shutdown_removes_only_what_mcp_itself_registered():
    """Two modules register tools now - mcp.py and memory.py.

    This used to snapshot the risk map at import and strip anything not in it,
    which meant whichever module imported first silently owned the other's entries
    and deleted them on shutdown. The symptom would have been `remember` refused as
    an unknown tool, mid-run, only when MCP happened to be on.
    """
    from agent import memory

    memory.activate()                       # registers `remember`
    mcp._REGISTERED.append("pretend_tool")  # as mcp.activate() would
    policy.register("pretend_tool", "read")

    mcp.shutdown()

    assert "pretend_tool" not in policy.RISK
    assert policy.RISK.get("remember") == "write", "mcp stripped memory's tool"
    memory.deactivate()


def test_shutdown_leaves_the_built_in_risk_map_intact():
    """With everything off the agent must be the four-tool one exactly."""
    builtins = {"read_file", "write_file", "edit_file", "run_shell"}
    mcp.shutdown()
    assert builtins <= set(policy.RISK)
