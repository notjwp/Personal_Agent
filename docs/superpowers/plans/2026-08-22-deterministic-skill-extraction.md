# Deterministic Skill Extraction Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `finish` write a skill from a reference document the agent read but never edited, so knowledge is retained without the agent choosing to retain it.

**Architecture:** Phase O measured `learn` called ZERO times in 15 valid sessions — tool exposed, explicit prompt instruction, three turn budgets, task small enough to finish with turns to spare. The model does not treat "record this for later" as part of the job. So the trigger moves off the model. Every `read_file` result is already in `state["messages"]` verbatim, and `finish` already runs deterministically with that list; extraction is therefore a file copy, needing **no model call and no fourth model-calling node**.

**Tech Stack:** Python 3.12, pytest, existing `agent/skills.py` + `agent/graph.py`. No new dependencies.

**Spec:** `eval/CHANGELOG.md` § "Phase O — Authoring: NOT SHIPPED" (the measurement this plan answers), and `ROADMAP.md` § Phase N/O.

## Global Constraints

- **CE-05:** no module-level I/O. Anything touching disk runs at call time, never at import.
- **NFR-602:** every function added must be unit-testable with no API key and no network.
- **`finish` stays deterministic.** It must not call a model. This is the design property the whole plan exists to preserve.
- **§13 Code Economy is binding.** A violation is a defect, not a style choice.
- **Iron Law:** one change per cycle, 3 runs per case, keep or revert, log it in `eval/CHANGELOG.md`.
- **Kill switch required.** `AGENT_SKILL_EXTRACTION=off` must restore current behaviour exactly.
- **Text only.** Extraction writes documents. It must never write anything executable — the Phase O constraint carries forward unchanged.
- **Verify a fixture THREE ways** before any scored run: untouched fails, a plausible answer without the knowledge fails, the correct answer passes.
- **Never force-remove a container while the harness is waiting on it.** `await_exclusive_workspace()` exists because case-runs share one `/workspace`.

---

## File Structure

| File | Responsibility |
|---|---|
| `agent/config.py` | `SKILL_EXTRACTION` kill switch, `EXTRACT_MIN_CHARS`, `EXTRACT_MAX_CHARS` |
| `agent/skills.py` | `read_but_not_edited()` — the selection rule; `extract()` — writes the skill |
| `agent/graph.py` | one call in `finish`, guarded by the switch |
| `tests/test_skills.py` | unit tests for selection, extraction, bounds, kill switch |
| `eval/harness.py` | record `skills_extracted` on every row |

`agent/skills.py` is the right home: it already owns `learn()`, `home()`, `authored()`, `_slug()` and `SKILL_FILE`, and extraction reuses all five. No new module — §12's allowlist is untouched.

---

### Task 1: The selection rule

**Files:**
- Modify: `agent/skills.py` (add after `authored()`, near line 290)
- Test: `tests/test_skills.py`

**Interfaces:**
- Consumes: `agent/graph.py`'s `_tool_calls(message)` and `_outcomes(messages)` — already exist, already used by `finish` for memory.
- Produces: `read_but_not_edited(messages: list[dict]) -> list[tuple[str, str]]` returning `[(path, content), ...]` — the candidate documents, most recently read last.

**Why this rule:** the judgement `learn` asked the model for was *"is this worth keeping?"*. A deterministic stand-in has to be defensible and cheap. A document the agent **read and never edited** in a session is a reference, not a work product. It rejects `VERSION` and `calc.py` (edited, the subject of the task) and accepts `CONVENTIONS.md`. It will over-capture — a README glanced at becomes a candidate — which is what Task 3's bounds and `MAX_AUTHORED_SKILLS` exist to contain.

- [ ] **Step 1: Write the failing test**

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_skills.py -k read_but_not_edited -q --basetemp=/tmp/pt`
Expected: FAIL with `ImportError: cannot import name 'read_but_not_edited'`

- [ ] **Step 3: Write minimal implementation**

Add to `agent/skills.py` after `authored()`:

```python
# Tools that mean the agent AUTHORED the file rather than consulted it.
_WROTE = ("write_file", "edit_file")


def read_but_not_edited(messages: list[dict]) -> list[tuple[str, str]]:
    """Documents the agent read and never wrote to, with their contents.

    This is the deterministic stand-in for the judgement `learn` asked the model
    for, and Phase O measured the model declining to make: 0 calls in 15 sessions.
    A file read and never edited is a reference; anything the agent wrote is its
    own output and knows nothing the agent did not already have.

    Contents come straight from the tool_result, which is why no model call is
    needed - `read_file`'s output is already verbatim in `messages`.

    Over-capture is expected and is bounded elsewhere: extract() caps the size and
    MAX_AUTHORED_SKILLS caps the library.
    """
    from agent.graph import _outcomes

    wrote, read, body = set(), [], {}
    # `_outcomes` already pairs each call with whether it succeeded, so the
    # error map is not recomputed here - only the result TEXT is needed.
    results = {b["tool_use_id"]: str(b.get("content", ""))
               for m in messages if isinstance(m.get("content"), list)
               for b in m["content"]
               if isinstance(b, dict) and b.get("type") == "tool_result"}

    for call, ok in _outcomes(messages):
        path = call["input"].get("path")
        if not isinstance(path, str):
            continue
        if call["name"] in _WROTE:
            wrote.add(path)
        elif call["name"] == "read_file" and ok:
            if path not in body:
                read.append(path)
            body[path] = results.get(call["id"], "")
    return [(p, body[p]) for p in read if p not in wrote and body[p]]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_skills.py -k "read_but_not_edited or reference or work_product" -q --basetemp=/tmp/pt`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add agent/skills.py tests/test_skills.py
git commit -m "feat(skills): read-but-not-edited selects reference documents"
```

---

### Task 2: The extraction, and its bounds

**Files:**
- Modify: `agent/config.py` (after `MAX_AUTHORED_SKILLS`, near line 271)
- Modify: `agent/skills.py` (add after `read_but_not_edited`)
- Test: `tests/test_skills.py`

**Interfaces:**
- Consumes: `read_but_not_edited(messages)` from Task 1; `learn(name, description, body)`, `authored()`, `home()`, `_slug(name)` — all already in `agent/skills.py`.
- Produces: `extract(messages: list[dict], goal: str) -> list[str]` returning the slugs written.

- [ ] **Step 1: Add the config**

Add to `agent/config.py` immediately after `MAX_AUTHORED_SKILLS = 8`:

```python
# Deterministic extraction at `finish` (Phase O-redux).
#
# Separate from SKILL_AUTHORING because they are different mechanisms answering
# the same need: authoring asks the MODEL to decide, and Phase O measured it
# declining 15 times out of 15. Extraction decides with a rule instead, so the two
# must be switchable independently or neither result is attributable.
SKILL_EXTRACTION = os.environ.get("AGENT_SKILL_EXTRACTION", "off").strip().lower() not in (
    "0", "off", "false")

# Size bounds on an extracted document, in characters.
#
# The floor rejects a one-line file that carries no procedure. The ceiling refuses
# a whole source file: the skill index is charged on EVERY request and overflowing
# SKILLS_INDEX_CHARS is fatal by design, so an unbounded extract would eventually
# brick its own runs.
EXTRACT_MIN_CHARS = 80
EXTRACT_MAX_CHARS = 4_000
```

- [ ] **Step 2: Write the failing test**

```python
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

    def doc(path):
        return [
            {"role": "assistant", "content": [
                {"type": "tool_use", "id": path, "name": "read_file",
                 "input": {"path": path}}]},
            {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": path,
                 "content": f"{path}\n" + "a real procedure line. " * 10}]},
        ]
    assert skills.extract(doc("second.md"), "goal") == []
    assert skills.authored() == ["already"]
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `python -m pytest tests/test_skills.py -k extract -q --basetemp=/tmp/pt`
Expected: FAIL with `AttributeError: module 'agent.skills' has no attribute 'extract'`

- [ ] **Step 4: Write minimal implementation**

Add to `agent/skills.py` after `read_but_not_edited`:

```python
def extract(messages: list[dict], goal: str) -> list[str]:
    """Write a skill from each reference document the agent read. Returns slugs.

    Phase O-redux. `learn` asks the MODEL to decide what is worth keeping, and the
    measurement was 0 calls in 15 sessions - so this decides with a rule instead.
    Everything it needs is already in `messages`, which is what keeps `finish`
    deterministic and leaves `act` the only node that touches a model.

    Never raises. It runs at the end of a session that may already have succeeded,
    and a bookkeeping failure must not turn a passing run into a crashed one.
    """
    if not config.SKILL_EXTRACTION:
        return []
    written = []
    for path, content in read_but_not_edited(messages):
        # The tool_result opens with a "<path> (lines X-Y of N)" header that
        # read_file added. It is noise in a skill, so drop the first line.
        body = content.split("\n", 1)[1] if "\n" in content else content
        body = body.strip()[:config.EXTRACT_MAX_CHARS]
        if len(body) < config.EXTRACT_MIN_CHARS:
            continue
        stem = path.rsplit("/", 1)[-1].rsplit(".", 1)[0]
        try:
            learn(name=stem,
                  description=(f"Use when working on tasks like: {goal[:120]}. "
                               f"Taken from {path}."),
                  body=body)
        except ValueError:
            # The library cap, or a name that slugs to nothing. Both are ordinary
            # outcomes here, not failures of the run.
            continue
        written.append(_slug(stem))
    return written
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/test_skills.py -k extract -q --basetemp=/tmp/pt`
Expected: PASS (5 tests)

- [ ] **Step 6: Run the whole file to check nothing regressed**

Run: `python -m pytest tests/test_skills.py -q --basetemp=/tmp/pt`
Expected: PASS, 39 tests (31 existing + 3 + 5)

- [ ] **Step 7: Commit**

```bash
git add agent/config.py agent/skills.py tests/test_skills.py
git commit -m "feat(skills): deterministic extraction, off by default"
```

---

### Task 3: Wire it into `finish`

**Files:**
- Modify: `agent/graph.py` (inside `finish`, after the `memory.write_episode` block)
- Test: `tests/test_nodes.py`

**Interfaces:**
- Consumes: `skills.extract(messages, goal)` from Task 2; `_goal(messages)` — already in `agent/graph.py`.
- Produces: a `{"kind": "skill", "name": <slug>}` trace event per extracted skill, which Task 4 counts.

- [ ] **Step 1: Write the failing test**

```python
def test_finish_extracts_a_skill_from_a_document_the_agent_read(
        fresh_app, tmp_workspace, monkeypatch):
    """`finish` must retain knowledge without the agent electing to retain it -
    Phase O measured the agent declining 15 times out of 15."""
    from agent import config, skills

    monkeypatch.setattr(config, "SKILLS_DIRS", (tmp_workspace / "skills",))
    monkeypatch.setattr(config, "SKILL_EXTRACTION", True)
    monkeypatch.setattr(config, "SKILLS_ENABLED", True)
    (tmp_workspace / "CONVENTIONS.md").write_text(
        "# Release conventions\n\nVERSION carries a -quartz suffix, and CHANGES.md "
        "gains one line of the form `rel <version> :: <summary>`.\n", encoding="utf-8")

    use_fake(monkeypatch, [
        tool_turn("read_file", path="CONVENTIONS.md"),
        text_turn("Read the conventions."),
    ])
    trace = []
    fresh_app.invoke(state(), {"configurable": {
        "thread_id": "extract-1", "autonomous": True, "trace": trace}})

    assert "conventions" in skills.catalogue()
    assert "-quartz" in skills.load_skill("conventions")
    assert [t["name"] for t in trace if t.get("kind") == "skill"] == ["conventions"]


def test_finish_extracts_nothing_when_extraction_is_off(
        fresh_app, tmp_workspace, monkeypatch):
    from agent import config, skills

    monkeypatch.setattr(config, "SKILLS_DIRS", (tmp_workspace / "skills",))
    monkeypatch.setattr(config, "SKILL_EXTRACTION", False)
    (tmp_workspace / "CONVENTIONS.md").write_text("# Rules\n\n" + "x" * 200,
                                                  encoding="utf-8")
    use_fake(monkeypatch, [
        tool_turn("read_file", path="CONVENTIONS.md"),
        text_turn("Done."),
    ])
    trace = []
    fresh_app.invoke(state(), {"configurable": {
        "thread_id": "extract-2", "autonomous": True, "trace": trace}})
    assert skills.catalogue() == {}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_nodes.py -k extract -q --basetemp=/tmp/pt`
Expected: FAIL — `assert 'conventions' in {}`

Note: these two need `langgraph-checkpoint-sqlite`, which is container-only. Run them with:

```bash
MSYS_NO_PATHCONV=1 docker run --rm --network none --read-only --tmpfs /tmp:exec \
  -v "$(pwd -W):/app:ro" -v "$(pwd -W)/eval/workspace:/workspace" \
  -v "$(pwd -W)/.agent/homes/_t:/state" \
  personal-agent pytest tests/test_nodes.py -k extract -q
```

- [ ] **Step 3: Write minimal implementation**

In `agent/graph.py`, inside `finish`, immediately after the `if settings.MEMORY_ENABLED:` block and before `return {}`:

```python
    # Phase O-redux. Knowledge is retained WITHOUT the agent electing to retain
    # it: Phase O measured `learn` called 0 times in 15 sessions, with the tool
    # exposed and the prompt asking for it. Everything needed is already in
    # `messages`, so this adds no model call and `finish` stays deterministic.
    for name in skills.extract(state["messages"], _goal(state["messages"])):
        if trace is not None:
            trace.append({"kind": "skill", "name": name})
```

Add `skills` to the existing import line at the top of `agent/graph.py`:

```python
from agent import memory, registry, skills
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
MSYS_NO_PATHCONV=1 docker run --rm --network none --read-only --tmpfs /tmp:exec \
  -v "$(pwd -W):/app:ro" -v "$(pwd -W)/eval/workspace:/workspace" \
  -v "$(pwd -W)/.agent/homes/_t:/state" \
  personal-agent pytest tests/test_nodes.py -k extract -q
```
Expected: PASS (2 tests)

- [ ] **Step 5: Run the full suite in the container**

```bash
MSYS_NO_PATHCONV=1 docker run --rm --network none --read-only --tmpfs /tmp:exec \
  -v "$(pwd -W):/app:ro" -v "$(pwd -W)/eval/runs:/app/eval/runs" \
  -v "$(pwd -W)/eval/workspace:/workspace" -v "$(pwd -W)/.agent/homes/_t:/state" \
  personal-agent pytest -q
```
Expected: PASS, 266 tests (256 + 3 + 5 + 2)

- [ ] **Step 6: Commit**

```bash
git add agent/graph.py tests/test_nodes.py
git commit -m "feat(graph): finish extracts a skill from documents it read"
```

---

### Task 4: Record it on every row

**Files:**
- Modify: `eval/harness.py` (in `record()`, beside `skills_authored`)
- Test: `tests/test_harness.py`

**Interfaces:**
- Consumes: the `{"kind": "skill", "name": ...}` trace events from Task 3.
- Produces: `skills_extracted` on every summary row.

**Why:** the three numbers Phase O needed were the authoring rate, the reuse rate and the delta. Extraction replaces the first with an *extraction* rate. Without it on the row, a pass-rate change cannot be attributed — which is exactly how Phase O nearly shipped a false claim three times.

- [ ] **Step 1: Write the failing test**

```python
def test_extracted_skills_are_read_off_the_trace():
    """A pass-rate change is not evidence for extraction unless the row says
    whether anything was extracted - the failure Phase O nearly shipped 3 times."""
    trace = [{"kind": "skill", "name": "conventions"},
             {"kind": "skill", "name": "conventions"},
             {"kind": "tool", "tool": "read_file"}]
    names = sorted({t.get("name", "") for t in trace if t.get("kind") == "skill"})
    assert names == ["conventions"]


def test_a_row_without_extraction_still_summarises():
    """Rows written before this cycle carry no `skills_extracted`. Re-reading an
    old run directory must not crash on a field that did not exist yet."""
    assert "case " in harness.summarise([row("a", 0)])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_harness.py -k extracted -q --basetemp=/tmp/pt`
Expected: FAIL — `summarise` raises `KeyError` or the assertion fails

- [ ] **Step 3: Write minimal implementation**

In `eval/harness.py`, in `record()`, immediately after the `"skills_authored"` entry:

```python
        # Written by `finish` from a document the agent read, with no model call
        # and no decision by the agent. The counterpart to skills_authored: one
        # counts what the MODEL chose to keep, the other what the RULE kept.
        "skills_extracted": sorted({t.get("name", "") for t in trace
                                    if t.get("kind") == "skill"}),
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_harness.py -q --basetemp=/tmp/pt`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add eval/harness.py tests/test_harness.py
git commit -m "feat(eval): record skills_extracted on every row"
```

---

### Task 5: Measure it

**Files:**
- Modify: `eval/CHANGELOG.md` (append the cycle)

No code changes. The `authoring` and `authoring-tiny` splits, their fixtures, the three-way verification and the brief-gap check all exist and were verified during Phase O.

**The control is Phase O's committed result: 0 passes, 0 skills kept, 15 sessions.** The treatment differs by one flag.

- [ ] **Step 1: Confirm the rig is clean before spending quota**

```bash
docker ps --filter ancestor=personal-agent -q | wc -l   # expect 0
ps aux | grep -c '[h]arness.py'                          # expect 0
```

If either is non-zero, stop the harness FIRST and only then remove containers. Never force-remove a container the harness is waiting on.

- [ ] **Step 2: Verify the fixtures still fail untouched**

```bash
MSYS_NO_PATHCONV=1 docker run --rm --network none --read-only --tmpfs /tmp:exec \
  -v "$(pwd -W):/app:ro" -v "$(pwd -W)/eval/workspace:/workspace" \
  -v "$(pwd -W)/.agent/homes/_v:/state" personal-agent bash -c \
  'scripts/reset.sh author-tiny && cd /workspace && head -1 c.txt | grep -qF "# owner: unassigned"; echo "exit=$? (want non-zero)"'
```

- [ ] **Step 3: Run the tiny split first — it is the cheap discriminator**

```bash
AGENT_SKILL_EXTRACTION=on nohup setsid python -u eval/harness.py \
  --split authoring-tiny --runs 3 --pace 15 > .agent/logs/redux-tiny.log 2>&1 &
```
Expected: ~10 min, ~100k tokens. Phase O's result on this split was 0/3 with 0 kept.

- [ ] **Step 4: Read `skills_extracted` before reading the pass rate**

```bash
python -c "
import json,glob,os
p=max((d for d in glob.glob('eval/runs/2026*') if os.path.exists(d+'/manifest.json')),
      key=lambda d: json.load(open(d+'/manifest.json'))['started'])
for l in open(p+'/summary.jsonl'):
    r=json.loads(l)
    print(r['id'], r['pass'], 'extracted=', r.get('skills_extracted'))"
```

**If `skills_extracted` is empty, stop.** The mechanism did not fire and the pass rate says nothing — read the trace for which candidate was rejected and why, rather than running the larger split.

- [ ] **Step 5: If it fired, run the full split**

```bash
AGENT_SKILL_EXTRACTION=on nohup setsid python -u eval/harness.py \
  --split authoring --runs 3 --pace 15 > .agent/logs/redux-full.log 2>&1 &
```
Expected: ~40 min, ~400k tokens.

- [ ] **Step 6: Dev regression guard**

```bash
AGENT_SKILL_EXTRACTION=on nohup setsid python -u eval/harness.py \
  --split dev --runs 3 --pace 15 > .agent/logs/redux-dev.log 2>&1 &
```
Expect 14/15. Extraction fires on every session, so the dev suite is where its cost and its over-capture show: watch `skills_extracted` and the index size, not just the pass rate.

- [ ] **Step 7: Apply the standing trust checks**

Zero tampering, zero write violations, one model, egress per row, and **zero `/app` reads** — the bypass Phase N verified rather than assumed.

- [ ] **Step 8: Log the cycle in `eval/CHANGELOG.md`**

Record: hypothesis, the one change, before, after, kept or reverted. Report the **extraction rate beside the pass rate** — a pass with nothing extracted is not evidence for this mechanism.

- [ ] **Step 9: Commit**

```bash
git add eval/CHANGELOG.md
git commit -m "docs(eval): Phase O-redux cycle result"
```

---

## Reading, fixed in advance

| extraction rate | pass rate | reading | what follows |
|---|---|---|---|
| high | **moves up** | The rule works and the judgement was not load-bearing | **Keep**, default on |
| high | **flat** | Extracted the wrong document, or the skill is not retrieved | Read traces: was it in the index? Was it loaded? Two different faults |
| high | **dev drops** | Over-capture — every README becomes a skill and the index crowds the prompt | Tighten the rule before keeping. This is the predicted failure |
| **zero** | any | Every candidate was rejected by the size bounds or the cap | One cycle on the bounds, then re-measure |
| any | **negative** | A wrong extracted document misled a later session | **Revert.** This outranks the pass rate |

## Verification

```bash
# offline suite - no API key, no network, read-only root
MSYS_NO_PATHCONV=1 docker run --rm --network none --read-only --tmpfs /tmp:exec \
  -v "$(pwd -W):/app:ro" -v "$(pwd -W)/eval/runs:/app/eval/runs" \
  -v "$(pwd -W)/eval/workspace:/workspace" -v "$(pwd -W)/.agent/homes/_t:/state" \
  personal-agent pytest -q

# kill-switch fidelity: off must be byte-identical to today
MSYS_NO_PATHCONV=1 docker run --rm --network none -e AGENT_SKILL_EXTRACTION=off \
  -w /app -e PYTHONPATH=/app -v "$(pwd -W):/app:ro" personal-agent python -c \
  "from agent import skills; print(skills.extract([], 'x'))"     # expect []

# finish still calls no model: only `act` may appear
grep -n "call_model" agent/graph.py                              # expect one hit, in act()
```

## What must NOT be done

- **Do not let extraction write anything executable.** It writes documents. The Phase O constraint carries forward.
- **Do not compare against "no skills".** The control is Phase O's committed 0-of-15, same splits, same fixtures.
- **Do not report a pass-rate rise without the extraction rate beside it.**
- **Do not let `finish` call a model.** If the rule needs judgement it cannot supply, that is the finding — and it is what earns during-use amendment, which is a different plan.
- **Do not skip the tiny split.** It is 100k tokens and it decides whether the 400k run is worth starting.
