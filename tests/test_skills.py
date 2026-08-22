"""Skills: progressive disclosure, and the document that must not become a hole.

An eighth test file, and the sixth stated deviation from the spec's three-file
`tests/` allowlist. The justification is specific to what this module is: **it is
the first component where a DOCUMENT can direct the agent's behaviour**, and its
failure modes are quiet ones. A skill dropped for a malformed field looks exactly
like a skill the agent chose not to use; a bundled-file path that escapes its
directory looks exactly like a successful read. Neither surfaces in a pass rate.

No API key, no network (NFR-602).
"""
import pytest

from agent import config, policy, skills

SKILL = """---
name: {name}
description: {description}
---

# {name}

{body}
"""


@pytest.fixture(autouse=True)
def library(tmp_path, monkeypatch):
    """A temp skill library, so tests never read the repository's real one."""
    monkeypatch.setattr(config, "SKILLS_DIRS", (tmp_path,))
    monkeypatch.setattr(config, "SKILLS_ENABLED", True)
    before = dict(policy.RISK)
    yield tmp_path
    skills.deactivate()
    policy.RISK.clear()
    policy.RISK.update(before)


def make(root, name, description="Use when testing.", body="Step one.", files=None):
    directory = root / name
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "SKILL.md").write_text(
        SKILL.format(name=name, description=description, body=body), encoding="utf-8")
    for filename, text in (files or {}).items():
        (directory / filename).write_text(text, encoding="utf-8")
    return directory


# ============================================================ level 1: the index

def test_the_index_carries_descriptions_but_not_bodies():
    """The entire progressive-disclosure claim, as an assertion.

    The body is what costs tokens; if it leaks into the index then every run pays
    for every skill whether or not it is used, which is the pattern this replaces.
    """
    make(library_root := _root(), "alpha", "Use when alpha.", "SECRET BODY TEXT")
    text = skills.index()
    assert "alpha" in text and "Use when alpha." in text
    assert "SECRET BODY TEXT" not in text
    assert library_root


def test_an_empty_library_costs_nothing():
    """No skills must mean no header and no tokens at all - not an empty section."""
    assert skills.index() == ""


def test_an_oversized_index_is_fatal_rather_than_truncated(monkeypatch):
    """Truncating drops whole skills out of the agent's view, and a skill it cannot
    see is indistinguishable in the traces from one it chose not to use."""
    root = _root()
    for i in range(10):
        make(root, f"skill{i}", "Use when " + "x" * 200)
    monkeypatch.setattr(config, "SKILLS_INDEX_CHARS", 300)
    with pytest.raises(skills.SkillIndexTooLarge) as caught:
        skills.index()
    assert "against a cap of" in str(caught.value)


# ====================================================== malformed skills survive

def test_a_skill_without_a_description_is_skipped_not_fatal():
    """A knowledge library that crashes the agent because one document has a typo
    is worse than no library - and a human editing these by hand will produce one."""
    root = _root()
    make(root, "good", "Use when good.")
    (root / "bad").mkdir()
    (root / "bad" / "SKILL.md").write_text("---\nname: bad\n---\nbody", encoding="utf-8")
    assert sorted(skills.catalogue()) == ["good"]


def test_invalid_yaml_is_skipped_not_fatal():
    root = _root()
    make(root, "good", "Use when good.")
    (root / "broken").mkdir()
    (root / "broken" / "SKILL.md").write_text(
        "---\nname: [unclosed\ndescription: x\n---\nbody", encoding="utf-8")
    assert "good" in skills.catalogue()


def test_a_directory_with_no_skill_file_is_ignored():
    root = _root()
    make(root, "good", "Use when good.")
    (root / "notaskill").mkdir()
    assert sorted(skills.catalogue()) == ["good"]


def test_a_long_prose_description_survives_the_parser():
    """`description` is prose with commas, colons and full stops. A split(":", 1)
    parser mangles it, and it is the exact field retrieval matches on."""
    wordy = ("Use when asked to deploy: staging, production, or a rollback. "
             'Covers "make ship-quartz" and the settled check.')
    make(_root(), "deploy", wordy)
    assert skills.catalogue()["deploy"]["description"] == wordy


# =========================================================== levels 2 and 3

def test_loading_returns_the_body():
    make(_root(), "alpha", body="Run the thing, then check it.")
    assert "Run the thing, then check it." in skills.load_skill("alpha")


def test_loading_lists_what_the_skill_bundles():
    """The agent cannot ask for a file it does not know exists."""
    make(_root(), "alpha", files={"codes.md": "# codes", "check.py": "print(1)"})
    body = skills.load_skill("alpha")
    assert "codes.md" in body and "check.py" in body
    assert "SKILL.md" not in body.split("Files bundled with this skill:")[1]


def test_a_bundled_file_can_be_read():
    make(_root(), "alpha", files={"codes.md": "QZ103 means something"})
    assert "QZ103" in skills.load_skill("alpha", file="codes.md")


def test_an_unknown_skill_names_what_does_exist():
    """An actionable error, the lesson the directory-read fix paid for."""
    make(_root(), "alpha")
    with pytest.raises(ValueError, match="alpha"):
        skills.load_skill("nope")


def test_an_unknown_bundled_file_is_actionable():
    make(_root(), "alpha")
    with pytest.raises(FileNotFoundError, match="bundles no file"):
        skills.load_skill("alpha", file="missing.md")


# ================================================= the escape, tested not assumed

@pytest.mark.parametrize("attempt", [
    "../../../etc/passwd",
    "/etc/passwd",
    "../other/SKILL.md",
    "sub/../../other/SKILL.md",
])
def test_a_bundled_path_cannot_escape_its_own_skill(attempt):
    """Phase L's PATH_ARGS lesson: the bypass is tested, never assumed closed.

    A skill that could read any file on disk would turn a document into a
    filesystem, and the failure would look exactly like a successful read.
    """
    root = _root()
    make(root, "alpha", files={"codes.md": "x"})
    make(root, "other", body="the other skill")
    with pytest.raises((ValueError, FileNotFoundError)):
        skills.load_skill("alpha", file=attempt)


def test_load_skill_never_executes_anything():
    """It READS. A bundled script is run by the agent calling run_shell on it, which
    goes through classify() and the DANGER regex like anything else. If loading
    could execute, a document would be a way around the gate."""
    make(_root(), "alpha", files={"boom.py": "raise SystemExit('should not run')"})
    assert "raise SystemExit" in skills.load_skill("alpha", file="boom.py")


# ================================================================ the gate

def test_activate_registers_the_tool_as_a_read():
    make(_root(), "alpha")
    skills.activate()
    assert policy.RISK["load_skill"] == "read"
    assert policy.classify("load_skill", {"name": "alpha"}, autonomous=True)[0] == "auto"


def test_deactivate_removes_only_its_own_registration():
    """Three modules register tools now. Any one stripping another's entries leaves
    a tool the gate refuses as unknown, mid-run."""
    policy.register("fetch", "read")
    skills.activate()
    skills.deactivate()
    assert "load_skill" not in policy.RISK
    assert policy.RISK.get("fetch") == "read"


# ============================================================== the kill switch

def test_with_skills_off_there_is_no_index_and_no_tool(monkeypatch):
    make(_root(), "alpha")
    monkeypatch.setattr(config, "SKILLS_ENABLED", False)
    assert skills.index() == ""
    assert skills.activate() == []
    assert skills.tools() == {}


def test_with_skills_off_the_toolset_falls_back(monkeypatch):
    from agent.registry import toolset

    make(_root(), "alpha")
    monkeypatch.setattr(config, "SKILLS_ENABLED", False)
    skills.activate()
    assert "load_skill" not in toolset()


def test_the_project_library_wins_a_name_collision(tmp_path, monkeypatch):
    """A skill the agent writes for itself in Phase O must not be able to shadow one
    shipped with the repository."""
    shipped, home = tmp_path / "shipped", tmp_path / "home"
    make(shipped, "alpha", "The shipped one.")
    make(home, "alpha", "The agent's own.")
    monkeypatch.setattr(config, "SKILLS_DIRS", (shipped, home))
    assert skills.catalogue()["alpha"]["description"] == "The shipped one."


# --------------------------------------------------------------------- helper

def _root():
    """The temp library root the autouse fixture installed."""
    return config.SKILLS_DIRS[0]


# ================================================== authoring (Phase O)

def test_learn_writes_a_loadable_skill(monkeypatch):
    """Written by the module, not the model, so the frontmatter is correct by
    construction - the malformed-document failure cannot happen to an authored one."""
    monkeypatch.setattr(config, "SKILL_AUTHORING", True)
    skills.learn(name="Cutting a Release",
                 description="Use when asked to cut or prepare a release.",
                 body="1. bump VERSION")
    cat = skills.catalogue()
    assert cat["cutting-a-release"]["description"].startswith("Use when asked to cut")
    assert "bump VERSION" in skills.load_skill("cutting-a-release")


@pytest.mark.parametrize("name", ["../../etc/passwd", "/abs/path", "a/b", r"..\x"])
def test_authoring_cannot_express_a_path(name, monkeypatch):
    """The enforcement is an ABSENT parameter plus a slug alphabet, not a rule that
    could be argued with: `learn` has no path argument at all."""
    monkeypatch.setattr(config, "SKILL_AUTHORING", True)
    skills.learn(name=name, description="Use when x.", body="step")
    for directory in skills.home().iterdir():
        assert directory.parent == skills.home()
        assert "/" not in directory.name and ".." != directory.name


def test_learn_refuses_a_skill_with_no_description(monkeypatch):
    monkeypatch.setattr(config, "SKILL_AUTHORING", True)
    with pytest.raises(ValueError, match="WHEN to use it"):
        skills.learn(name="x", description="  ", body="step")


def test_learn_caps_the_library(monkeypatch):
    """The index is charged on every request and overflowing it is fatal, so an
    agent writing one skill per session would otherwise brick its own runs."""
    monkeypatch.setattr(config, "SKILL_AUTHORING", True)
    monkeypatch.setattr(config, "MAX_AUTHORED_SKILLS", 2)
    skills.learn(name="one", description="Use when one.", body="a")
    skills.learn(name="two", description="Use when two.", body="b")
    with pytest.raises(ValueError, match="the limit"):
        skills.learn(name="three", description="Use when three.", body="c")


def test_rewriting_is_allowed_but_the_cap_still_applies(monkeypatch):
    """Rewriting must stay possible at the cap, or the agent is stuck with its own
    early mistakes and cannot correct them."""
    monkeypatch.setattr(config, "SKILL_AUTHORING", True)
    monkeypatch.setattr(config, "MAX_AUTHORED_SKILLS", 1)
    skills.learn(name="one", description="Use when one.", body="first")
    skills.learn(name="one", description="Use when one, revised.", body="second")
    assert "second" in skills.load_skill("one")
    assert skills.authored() == ["one"]


def test_authoring_off_removes_the_tool_but_not_loading(monkeypatch):
    """Phase O's control: loading ON, authoring OFF. One flag has to move without
    the other or the comparison measures the wrong thing."""
    monkeypatch.setattr(config, "SKILL_AUTHORING", False)
    skills.activate()
    assert "learn" not in skills.tools()
    assert "load_skill" in skills.tools()
    assert "learn" not in policy.RISK


# ============================ deterministic extraction (Phase O-redux)

def test_read_but_not_edited_keeps_references_and_drops_work_products():
    """The judgement `learn` asked the model for, as a rule: a document read and
    never edited is a reference; anything the agent wrote is a work product."""
    from agent.skills import read_but_not_edited

    messages = [
        {"role": "user", "content": "do the thing"},
        {"role": "assistant", "content": [
            {"type": "tool_use", "id": "a", "name": "read_file",
             "input": {"path": "CONVENTIONS.md"}},
            {"type": "tool_use", "id": "b", "name": "read_file",
             "input": {"path": "VERSION"}},
            {"type": "tool_use", "id": "c", "name": "edit_file",
             "input": {"path": "VERSION", "old_string": "1", "new_string": "2"}}]},
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "a",
             "content": "CONVENTIONS.md (lines 1-2 of 2)\n     1\t# Rules\n     2\tuse tabs"},
            {"type": "tool_result", "tool_use_id": "b",
             "content": "VERSION (lines 1-1 of 1)\n     1\t1.0"},
            {"type": "tool_result", "tool_use_id": "c", "content": "ok"}]},
    ]
    found = read_but_not_edited(messages)
    assert [path for path, _ in found] == ["CONVENTIONS.md"]
    assert "use tabs" in found[0][1]


def test_a_failed_read_is_not_a_candidate():
    """A read that errored has no content to keep."""
    from agent.skills import read_but_not_edited

    messages = [
        {"role": "assistant", "content": [
            {"type": "tool_use", "id": "a", "name": "read_file",
             "input": {"path": "gone.md"}}]},
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "a",
             "content": "FileNotFoundError: gone.md", "is_error": True}]},
    ]
    assert read_but_not_edited(messages) == []


def test_a_file_written_then_read_is_not_a_reference():
    """write_file counts as editing. The agent's own output is not knowledge."""
    from agent.skills import read_but_not_edited

    messages = [
        {"role": "assistant", "content": [
            {"type": "tool_use", "id": "a", "name": "write_file",
             "input": {"path": "notes.md", "content": "x"}},
            {"type": "tool_use", "id": "b", "name": "read_file",
             "input": {"path": "notes.md"}}]},
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "a", "content": "ok"},
            {"type": "tool_result", "tool_use_id": "b", "content": "notes.md\n     1\tx"}]},
    ]
    assert read_but_not_edited(messages) == []


def test_extract_writes_a_loadable_skill_from_a_document(monkeypatch):
    """The whole mechanism: what the agent READ becomes what a later session LOADS,
    with no model call anywhere in the path."""
    monkeypatch.setattr(config, "SKILL_EXTRACTION", True)
    messages = [
        {"role": "user", "content": "Read CONVENTIONS.md, then cut release 4.12.0."},
        {"role": "assistant", "content": [
            {"type": "tool_use", "id": "a", "name": "read_file",
             "input": {"path": "CONVENTIONS.md"}}]},
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "a",
             "content": "CONVENTIONS.md\n# Release conventions\n"
                        "VERSION carries a -quartz suffix. CHANGES.md gains "
                        "`rel <version> :: <summary>` and nothing else."}]},
    ]
    written = skills.extract(messages, "Read CONVENTIONS.md, then cut release 4.12.0.")
    assert written == ["conventions"]
    assert "-quartz" in skills.load_skill("conventions")
    assert "conventions" in skills.catalogue()


def test_extract_skips_a_document_too_short_to_carry_a_procedure(monkeypatch):
    monkeypatch.setattr(config, "SKILL_EXTRACTION", True)
    messages = [
        {"role": "assistant", "content": [
            {"type": "tool_use", "id": "a", "name": "read_file",
             "input": {"path": "a.txt"}}]},
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "a", "content": "a.txt\n  1\talpha"}]},
    ]
    assert skills.extract(messages, "goal") == []


def test_extract_truncates_rather_than_overflowing_the_index(monkeypatch):
    """An unbounded extract would eventually breach SKILLS_INDEX_CHARS, which is
    fatal by design - so the bound belongs here, before the file is written."""
    monkeypatch.setattr(config, "SKILL_EXTRACTION", True)
    monkeypatch.setattr(config, "EXTRACT_MAX_CHARS", 200)
    huge = "rules.md\n" + ("a procedure line that repeats. " * 100)
    messages = [
        {"role": "assistant", "content": [
            {"type": "tool_use", "id": "a", "name": "read_file",
             "input": {"path": "rules.md"}}]},
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "a", "content": huge}]},
    ]
    assert skills.extract(messages, "goal") == ["rules"]
    assert len(skills.load_skill("rules")) < 500


def test_extract_is_a_no_op_when_switched_off(monkeypatch):
    """A capability that cannot be turned off cannot be attributed either."""
    monkeypatch.setattr(config, "SKILL_EXTRACTION", False)
    messages = [
        {"role": "assistant", "content": [
            {"type": "tool_use", "id": "a", "name": "read_file",
             "input": {"path": "CONVENTIONS.md"}}]},
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "a",
             "content": "CONVENTIONS.md\n" + "x" * 200}]},
    ]
    assert skills.extract(messages, "goal") == []
    assert skills.authored() == []


def test_extract_respects_the_library_cap(monkeypatch):
    """learn() raises at the cap; extract must absorb that rather than crash a run
    that had otherwise succeeded."""
    monkeypatch.setattr(config, "SKILL_EXTRACTION", True)
    monkeypatch.setattr(config, "MAX_AUTHORED_SKILLS", 1)
    skills.learn(name="already", description="Use when already.", body="step")
    messages = [
        {"role": "assistant", "content": [
            {"type": "tool_use", "id": "a", "name": "read_file",
             "input": {"path": "second.md"}}]},
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "a",
             "content": "second.md\n" + "a real procedure line. " * 10}]},
    ]
    assert skills.extract(messages, "goal") == []
    assert skills.authored() == ["already"]


def test_the_extracted_body_has_no_line_number_gutter(monkeypatch):
    """read_file numbers every line for the model's benefit. Those numbers are
    formatting, not knowledge, and they must not end up inside a skill."""
    monkeypatch.setattr(config, "SKILL_EXTRACTION", True)
    messages = [
        {"role": "assistant", "content": [
            {"type": "tool_use", "id": "a", "name": "read_file",
             "input": {"path": "CONVENTIONS.md"}}]},
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "a",
             "content": "CONVENTIONS.md (lines 1-3 of 3)\n"
                        "     1\t# The one rule\n"
                        "     2\tEvery file must open with `# owner: unassigned`.\n"
                        "     3\tThat is the whole convention, and nothing else."}]},
    ]
    skills.extract(messages, "goal")
    body = skills.load_skill("conventions")
    assert "# The one rule" in body
    assert "owner: unassigned" in body
    assert "\t" not in body, "the line-number gutter survived into the skill"
    assert "     1" not in body


def test_the_description_says_WHEN_not_what_the_agent_happened_to_be_doing(monkeypatch):
    """The description is all a later session sees until it opens the skill, so it
    must describe a CLASS of work. Derived from the goal it named one specific task
    ('create b.txt containing beta'), and a later task asking for c.txt matched
    nothing - measured: extracted and indexed, never loaded."""
    monkeypatch.setattr(config, "SKILL_EXTRACTION", True)
    goal = "Read CONVENTIONS.md, then create a.txt's companion file b.txt containing beta."
    messages = [
        {"role": "user", "content": goal},
        {"role": "assistant", "content": [
            {"type": "tool_use", "id": "a", "name": "read_file",
             "input": {"path": "CONVENTIONS.md"}}]},
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "a",
             "content": "CONVENTIONS.md\n# The one rule\n"
                        "Every file must open with `# owner: unassigned`. That is all."}]},
    ]
    skills.extract(messages, goal)
    description = skills.catalogue()["conventions"]["description"]
    assert "b.txt" not in description and "beta" not in description
    assert "CONVENTIONS.md" in description
