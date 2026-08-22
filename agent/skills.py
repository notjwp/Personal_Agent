"""On-demand knowledge documents, in the agentskills.io layout (Phase N).

A skill is a directory holding `SKILL.md` - YAML frontmatter with `name` and
`description`, then a markdown body - plus any reference files or scripts it
bundles.

**Progressive disclosure is the entire point, and on this provider it is not a
nicety.** Phase L measured `cache_read_tokens: 0` on every row of a scored run,
so everything in the prompt is re-sent and re-paid on every turn; four tool
schemas were already ~23% of a median run. A knowledge library injected wholesale
would be ruinous. So:

    Level 1  always loaded   name + description, ~40 tokens per skill
    Level 2  on demand       the SKILL.md body
    Level 3  on demand       bundled reference files

The cost of a skill is paid only when the agent decides it applies.

**This module READS. It never executes.** A bundled script is run by the agent
calling `run_shell` on it, which goes through classify() and the DANGER regex like
anything else. That grants nothing `run_shell` does not already grant inside a
container whose boundary Phase K made kernel-enforced - what Level 3 changes is
PROVENANCE, code arriving in a document rather than from the model. In Phase N
every skill is written by a human; **when Phase O lets the agent author them, this
becomes a live question and must be re-opened.**

Retrieval is keyword-shaped and stays that way: §11 forbids vectors until keyword
recall is measured and found wanting, and Phase M measured keyword recall working.

CE-05: nothing here runs at import.
NFR-602: every function below is testable with no API key and no network.
"""
from __future__ import annotations

import sys
from pathlib import Path

from agent import config, policy

# Names this module put into policy.RISK, so deactivate() removes exactly those.
# Per-module, like mcp.py and memory.py: a snapshot taken by whichever imported
# first would silently own the others' entries and strip them on shutdown.
_REGISTERED: list[str] = []

SKILL_FILE = "SKILL.md"


def _scan(front: str) -> dict:
    """Last-resort frontmatter read: the first colon on each line wins.

    Exists because of a defect its own test caught. `description` is prose, and
    prose contains colons:

        description: Use when asked to deploy: staging, production, or rollback.

    That is not valid YAML - the value has to be quoted - and `safe_load` either
    raises or returns something unusable, so the skill was DROPPED IN SILENCE. A
    human writing these by hand will produce that line, and a skill the agent
    cannot see is indistinguishable from one it chose not to use.

    So YAML stays the primary path, and this recovers the common mistake rather
    than punishing it.
    """
    meta = {}
    for line in front.splitlines():
        key, sep, value = line.partition(":")
        key = key.strip()
        if sep and key in ("name", "description") and key not in meta:
            meta[key] = value.strip().strip("\"'")
    return meta


def _parse(text: str) -> tuple[dict, str]:
    """Split YAML frontmatter from the body.

    A real YAML parser rather than a hand-rolled split: the standard's frontmatter
    IS YAML, and quoted or folded values are only read correctly by one. `_scan`
    picks up what safe_load cannot.
    """
    import yaml

    if not text.startswith("---"):
        return {}, text
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}, text
    front, body = parts[1], parts[2].lstrip("\n")
    try:
        meta = yaml.safe_load(front)
    except yaml.YAMLError:
        meta = None
    if not isinstance(meta, dict) or not str(meta.get("description") or "").strip():
        meta = {**_scan(front), **(meta if isinstance(meta, dict) else {})}
    return meta, body


def _read(directory: Path) -> dict | None:
    """One skill, or None when the directory does not describe a usable one.

    Skipped rather than raised. A knowledge library that takes down the agent
    because one document has a typo is worse than no library - and a malformed
    skill is exactly what a human editing these files by hand will produce.
    """
    path = directory / SKILL_FILE
    try:
        meta, body = _parse(path.read_text(encoding="utf-8", errors="replace"))
    except Exception:                       # unreadable, or invalid YAML
        return None
    name = str(meta.get("name") or directory.name).strip()
    description = str(meta.get("description") or "").strip()
    if not name or not description:
        # Without a description there is nothing to match on, so the skill could
        # never be chosen anyway. Dropping it keeps the index honest.
        return None
    return {"name": name, "description": description, "body": body, "dir": directory}


def catalogue() -> dict[str, dict]:
    """Every usable skill, keyed by name. Later roots do NOT override earlier ones.

    The project's own skills win over the agent home's, so a skill the agent
    writes for itself in Phase O cannot silently shadow one shipped with the
    repository.
    """
    found: dict[str, dict] = {}
    if not config.SKILLS_ENABLED:
        return found
    for root in config.SKILLS_DIRS:
        if not root.is_dir():
            continue
        for directory in sorted(p for p in root.iterdir() if p.is_dir()):
            skill = _read(directory)
            if skill and skill["name"] not in found:
                found[skill["name"]] = skill
    return found


class SkillIndexTooLarge(RuntimeError):
    """The always-loaded index costs more than config.SKILLS_INDEX_CHARS allows.

    Fatal, and deliberately not a truncation. Cutting the list short drops whole
    skills out of the agent's view, and a skill it cannot see is indistinguishable
    in the traces from one it chose not to use - so the failure would land as a
    quiet drop in the load rate that nobody could attribute. The same reasoning
    makes MAX_SCHEMA_CHARS fatal: a budget that can be exceeded with a printed
    warning is not a budget.
    """


def index() -> str:
    """Level 1: what is always in the prompt. Name and description only.

    Paid on every request of every run, including runs that load nothing, which is
    why it is budgeted rather than merely bounded.
    """
    skills = catalogue()
    if not skills:
        return ""
    lines = [f"- {s['name']}: {s['description']}" for s in skills.values()]
    text = ("# Skills available\n\n"
            "Reference documents you can open when one applies. Call "
            '`load_skill(name="...")` to read one BEFORE starting work it covers - '
            "the descriptions below are all you have until you do.\n\n"
            + "\n".join(lines))
    if len(text) > config.SKILLS_INDEX_CHARS:
        raise SkillIndexTooLarge(
            f"the skill index is {len(text):,} chars against a cap of "
            f"{config.SKILLS_INDEX_CHARS:,} ({len(skills)} skills). It sits in the "
            f"system prompt and is re-sent on every request, so this is charged per "
            f"turn. Shorten the `description` fields, remove a skill, or raise the "
            f"cap in config.py deliberately.")
    return text


def _resolve(skill: dict, filename: str) -> Path:
    """A bundled file inside this skill's own directory, or an error.

    Same shape as policy._inside_workspace(): resolve, then require the root to be
    an ancestor. `.resolve()` follows symlinks, so a link inside the skill pointing
    out resolves outside and is refused. Fails closed.
    """
    root = skill["dir"].resolve()
    candidate = Path(filename)
    target = (candidate if candidate.is_absolute() else root / candidate).resolve()
    if target != root and root not in target.parents:
        raise ValueError(
            f"{filename!r} is outside the skill directory. Bundled files are named "
            f"relative to the skill, for example file=\"reference.md\".")
    if not target.is_file():
        raise FileNotFoundError(
            f"{skill['name']} bundles no file called {filename!r}. "
            f"Call load_skill(name=\"{skill['name']}\") to list what it does bundle.")
    return target


def load_skill(name: str, file: str | None = None) -> str:
    """Levels 2 and 3, in one tool.

    Without `file`: the skill's body, plus a list of what it bundles.
    With `file`: that bundled file's contents.

    One tool rather than two because the schema budget allowed exactly one more
    (six tools were 3,553 chars against a 6,000 cap) and a second would have spent
    the headroom for no capability.
    """
    skills = catalogue()
    skill = skills.get(str(name).strip())
    if skill is None:
        raise ValueError(
            f"no skill called {name!r}. Available: "
            f"{', '.join(sorted(skills)) or 'none'}.")

    if file:
        return _resolve(skill, str(file)).read_text(encoding="utf-8", errors="replace")

    bundled = sorted(p.name for p in skill["dir"].iterdir()
                     if p.is_file() and p.name != SKILL_FILE)
    body = skill["body"]
    if bundled:
        body += ("\n\n---\nFiles bundled with this skill: " + ", ".join(bundled) +
                 f'\nRead one with load_skill(name="{skill["name"]}", file="...").'
                 "\nTo RUN one, use run_shell against "
                 f"{skill['dir'].as_posix()}/<file>.")
    return body


LOAD_SCHEMA = {
    "name": "load_skill",
    "description": (
        "Open one of the skills listed under 'Skills available'. Returns the full "
        "document, which the listed description only summarises. Pass `file` to "
        "read something the skill bundles alongside it."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "The skill's name, exactly as listed."},
            "file": {"type": "string",
                     "description": ("Optional. A file bundled with the skill, named "
                                     "relative to it, e.g. \"reference.md\".")},
        },
        "required": ["name"],
    },
}


def unusable() -> list[str]:
    """Directories that look like skills but cannot be used, for reporting."""
    broken = []
    for root in config.SKILLS_DIRS:
        if not root.is_dir():
            continue
        for directory in sorted(p for p in root.iterdir() if p.is_dir()):
            if (directory / SKILL_FILE).is_file() and _read(directory) is None:
                broken.append(directory.name)
    return broken


def activate() -> list[str]:
    """Register the skill tool for this run. A no-op when skills are off."""
    if not config.SKILLS_ENABLED:
        return []
    # Said once, at startup, rather than never. A silently dropped skill looks
    # exactly like one the agent decided not to open.
    broken = unusable()
    if broken:
        print(f"skills: ignoring {len(broken)} unusable document(s): "
              f"{', '.join(broken)} (each needs `name` and `description`)",
              file=sys.stderr)
    if "load_skill" not in _REGISTERED:
        # `read`: it opens documents and cannot modify anything. Running a bundled
        # script is a separate run_shell call, classified on its own merits.
        policy.register("load_skill", "read")
        _REGISTERED.append("load_skill")
    return sorted(catalogue())


def deactivate() -> None:
    """Remove exactly what activate() registered. Safe to call unconditionally."""
    while _REGISTERED:
        policy.RISK.pop(_REGISTERED.pop(), None)


def tools() -> dict[str, dict]:
    """The skill tools active for this run. Empty when skills are off."""
    if not config.SKILLS_ENABLED or "load_skill" not in _REGISTERED:
        return {}
    return {"load_skill": {"fn": load_skill, "schema": LOAD_SCHEMA, "risk": "read"}}
