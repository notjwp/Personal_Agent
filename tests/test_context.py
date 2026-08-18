"""shrink() — truncate, spill, and tell the model how to look at the rest."""
from agent import config
from agent.context import shrink


def test_under_cap_returned_verbatim(tmp_workspace):
    assert shrink("read_file", "short output") == "short output"
    assert not config.ARTIFACTS.exists(), "nothing should be spilled under the cap"


def test_exactly_at_cap_is_not_spilled(tmp_workspace):
    text = "x" * config.TOOL_CAPS["run_shell"]
    assert shrink("run_shell", text) == text
    assert not config.ARTIFACTS.exists()


def test_over_cap_spills_and_instructs(tmp_workspace):
    text = "\n".join(f"line {i}" for i in range(5000))
    out = shrink("run_shell", text)

    # 1. bounded
    assert len(out) < config.MAX_RESULT_CHARS + 600
    # 2. head and tail survive
    assert "line 0" in out and "line 4999" in out
    # 3. elision marker
    assert "elided" in out
    # 4. the full text is on disk, intact
    artifacts = list(config.ARTIFACTS.glob("*.txt"))
    assert len(artifacts) == 1
    assert artifacts[0].read_text(encoding="utf-8") == text
    # 5. the path is IN the returned string
    assert str(artifacts[0]) in out
    # 6. and so are instructions for using it - a bare path is ignored in practice
    assert "read_file" in out and "grep" in out


def test_single_giant_line_is_still_bounded(tmp_workspace):
    """Fewer lines than head+tail, so the line-based path cannot apply."""
    out = shrink("run_shell", "x" * 100_000)
    assert len(out) < config.MAX_RESULT_CHARS + 600
    assert list(config.ARTIFACTS.glob("*.txt"))


def test_per_tool_caps_are_honoured(tmp_workspace):
    """write_file has a much smaller cap than run_shell."""
    text = "y" * 3000
    assert len(shrink("write_file", text)) < len(text)   # over write_file's 400
    assert shrink("run_shell", text) == text             # under run_shell's 6000


def test_unknown_tool_falls_back_to_the_ceiling(tmp_workspace):
    assert shrink("mystery", "z" * 100) == "z" * 100


def test_identical_output_reuses_one_artifact(tmp_workspace):
    text = "\n".join(f"line {i}" for i in range(5000))
    shrink("run_shell", text)
    shrink("run_shell", text)
    assert len(list(config.ARTIFACTS.glob("*.txt"))) == 1, "content-addressed naming"


def test_no_artifacts_directory_created_at_import(tmp_workspace):
    """CE-05: no module-level I/O. Importing must not touch the filesystem."""
    import importlib

    import agent.context
    importlib.reload(agent.context)
    assert not config.ARTIFACTS.exists()
