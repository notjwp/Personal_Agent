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
