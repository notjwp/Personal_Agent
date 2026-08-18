# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Repository state

Phases A-D are built and committed (`131fd4f`). What exists: the measurement rig, the
`act -> gate -> execute -> reflect` loop, a two-provider model adapter, the interactive CLI with
approval and resume, and a committed baseline.

- **Baseline: 4/15**, 3 runs per dev case, on `nvidia/meta/llama-3.1-70b-instruct`. Per-case table in
  `README.md`; conditions and trust checks in `eval/CHANGELOG.md`.
- **150 unit tests**, green with no API key and no network.
- **Tuning cycle 1 is reverted.** Gating `done` on a successful command worked mechanically - it
  produced the first `stuck` verdicts in the project's history - but made the score worse: the agent
  satisfied the gate by rewriting tests until pytest exited 0, and tampering went 5/15 to 5/5. **The
  binding constraint is blind editing, not termination.** 9 of 15 baseline runs never called
  `read_file` at all.
- Still deferred and unearned: compaction, plan node, memory, web, worker. The baseline's verdict
  distribution (`done x15`) earns none of them.
- **The next phase is E (durability and safety)**, chosen because it closes Definition-of-Done items
  and needs almost no model quota, while the model-capability question stays open.

## CONTEXT.md is the authority

`CONTEXT.md` is the binding build specification for this project, not background reading. Its own
precedence rules:

- **§9 is the only build order.** Sections 5–7 are the full requirement set for the *finished*
  system and must not be implemented top to bottom. Anything marked `[S]` or `[C]` is out of scope
  until the `[M]` set passes evaluation.
- **§12 is a file allowlist.** Create the files it lists and no others. Deferred files
  (`agent/registry.py`, `memory.py`, `web.py`, `worker.py`) are created only when their layer is
  earned, with the stated trigger (e.g. the `@tool` decorator arrives at tool six, not before).

  **Four stated deviations exist, each justified in writing rather than assumed:**
  `agent/provider.py` (earned by a SECOND implementation - Anthropic plus any OpenAI-compatible
  endpoint - which is exactly what CE-01 requires), and three extra test files beyond the allowed
  three: `tests/test_nodes.py` (§10 demands unit tests for every deterministic node),
  `tests/test_cli.py` (the approval prompt is where consent is decided), and
  `tests/test_harness.py` (its functions decide the headline number, and a wrong denominator does
  not crash - it silently reports something other than the truth). Do not add a fifth without the
  same standard of justification.
- **§13 (Code Economy) is binding.** A violation is a defect, not a style choice.
- **Where §3/§4 and §13 disagree, §13 governs.** §3 is the logical flow; §13 is the code shape.
- When a requirement and existing code disagree, the requirement wins — say so rather than
  silently reinterpreting it.

## Architecture

A single-user autonomous agent: goal in natural language → plan → tool-calling loop → terminal
verdict. Three properties distinguish it from function-calling chat, and each one is a layer you
must not collapse:

- **Policy gate.** Every tool call is classified `auto` / `confirm` / `deny` *before* any side
  effect. `classify()` in `policy.py` is pure — no logging, no counters, no DB writes.
- **Context manager.** No tool output reaches the model unfiltered. `shrink()` in `context.py`
  caps each result, spills the full output to `.agent/artifacts/<id>.txt`, and returns a plain
  string containing head lines, an elision marker, tail lines, the artifact path, **and
  instructions for inspecting it** — a bare path is ignored by the model in practice.
- **Checkpointing.** State is written after every node transition, keyed by `thread_id`. A kill
  loses at most one node; resume is re-invocation with the same id, not a restart.

Graph: `act → gate → execute(+observe) → reflect`, looping until `done` / `stuck` / budget / turn
cap, then `finish`. Only three nodes ever call the model (plan, act, compact-summarisation).
Everything else is ordinary code and **must be unit-testable without an API key** (NFR-602) — this
ratio is the most important design property in the system, so resist pushing logic into prompts.

**`gate` and `execute` must never merge** (CE-07). `gate` suspends on `interrupt()` and re-runs
from its first line on resume; merged, every already-executed tool fires a second time. This is
also why nothing upstream of a suspension point may have side effects (FR-305).

`config.py` is the single source of truth for the workspace root, model, per-tool output caps,
turn/token budgets, compaction threshold, and head/tail line counts. FR-302 and NFR-201 both
depend on the workspace root having exactly one definition — never re-derive it in another module.

Prompts live in `prompts/SOUL.md`, version-controlled, never as string literals (NFR-603), and are
read **inside** the node, never at import time (CE-05).

## Spec-mandated fixes to the v1 skeleton

§9 Step 2 lists five defects in the skeleton design that must be corrected on the way in. They are
easy to reintroduce because each looks correct in isolation:

- **Entry edge** is `START → act`, not `START → plan` (there is no plan node in v1).
- **Termination guard.** With no plan node, `cursor + 1 >= len(plan)` is `1 >= 0` and returns
  `done` on the first text-only reply. Gate `done` on whether any tool call was ever made; restore
  the cursor check only when the plan node lands.
- **`failures` is a plain `int` with overwrite semantics**, counting *consecutive* failed turns —
  reset to 0 when every result succeeded. As an accumulating list it latches at `>= 3` forever.
- **Risk map.** Either relabel `run_shell` as `"write"` or make `RISK` the single path in
  `classify()`. A name special-case that returns before consulting `RISK` makes the declaration
  dead, and any tool later marked `destructive` would be silently auto-denied during eval.
- **Tracing is in scope from step 2**, not deferred: the harness writes
  `eval/runs/<timestamp>/<case-id>.json` with the full final message list plus per-call tool name,
  verdict, duration, input/output bytes, and spill path. Step 4 is unactionable without it.

State shape for v1 is fixed in §13: a plain `TypedDict`, no reducers, no `Annotated`, no `operator`
import. Nodes return the full `messages` list; compaction is then an ordinary return.

## Commands

```bash
python -m agent "goal"            # interactive run; destructive calls pause for approval
python -m agent --list            # past threads, newest first, with verdict and turn count
python -m agent --resume <id>     # continue a thread; a task's identity IS its thread id

python eval/harness.py --split dev --runs 3 --pace 20   # a baseline; --pace throttles the free tier
python eval/harness.py --split dev --runs 3 --pace 20 --continue   # resume an interrupted baseline
python eval/harness.py --case fix-import --runs 3        # one case, repeated

scripts/reset.sh <case-id>        # restore /workspace to a fixture's known state (idempotent)
pytest                            # 150 unit tests, no API key, no network
pytest tests/test_policy.py -k escape   # single test
```

**Never pipe the harness through `tail`.** It buffers until exit, which makes a hang
indistinguishable from progress. This project has lost time to it twice.

A run that never reached the model is reported **blocked** and excluded from the score rather than
counted as a failure - `pass 4/13, 2 blocked`, never `pass 4/15`. Blocked runs are retried
automatically, and `--continue` re-runs exactly the case-runs with no result.

Sandbox image is built from `Containerfile` (python:3.12-slim + git + pytest + flask + langgraph)
with the workspace as the only bind mount (NFR-204).

## Evaluation discipline

The pass rate is the project's headline number, and §9 Step 4 constrains how it is moved:

- **One change per cycle.** Two changes and the delta cannot be attributed.
- Prompt changes are changes and are measured like code changes.
- **Revert anything that does not move the number**, including changes that "seem right".
- Always report pass counts across 3 seeds, never a single run — 1-of-3 is not a passing case.
- Log every cycle in `eval/CHANGELOG.md`: hypothesis, change, before, after, kept or reverted.
- Do not look at held-out traces during tuning; run the held-out 10 only at milestones.
- A requirement is satisfied when the cases exercising it pass, not when the code exists.

If a §9 step overruns its estimate by more than double, stop and reduce scope rather than pushing
through.

## Environment notes

Target runtime is Fedora natively or Windows under WSL2, with execution confined to a container
(Windows-native support outside WSL2 is an explicit non-goal, §11). This development machine is
Windows 11 with Docker 29.7 and Git Bash; the only WSL distro currently registered is
`docker-desktop`, so container execution — not a native Linux shell — is the path that exists here.
`.agent/` is runtime state and must be gitignored.

## Working guidelines

Behavioral rules that reduce common LLM coding mistakes. These govern *how* to work; the sections
above govern *what* this project is.

### 1. Think before coding

Don't assume. Don't hide confusion. Surface tradeoffs. Before implementing:

- State assumptions explicitly. If uncertain, ask.
- If multiple interpretations exist, present them — don't pick silently.
- If a simpler approach exists, say so. Push back when warranted.
- If something is unclear, stop. Name what's confusing. Ask.

### 2. Simplicity first

Minimum code that solves the problem. Nothing speculative.

- No features beyond what was asked.
- No abstractions for single-use code.
- No "flexibility" or "configurability" that wasn't requested.
- No error handling for impossible scenarios.
- If you write 200 lines and it could be 50, rewrite it.

Ask: *"Would a senior engineer say this is overcomplicated?"* If yes, simplify.

### 3. Surgical changes

Touch only what you must. Clean up only your own mess.

When editing existing code:

- Don't "improve" adjacent code, comments, or formatting.
- Don't refactor things that aren't broken.
- Match existing style, even if you'd do it differently.
- If you notice unrelated dead code, mention it — don't delete it.

When your changes create orphans:

- Remove imports/variables/functions that YOUR changes made unused.
- Don't remove pre-existing dead code unless asked.

**The test:** every changed line should trace directly to the user's request.

### 4. Goal-driven execution

Define success criteria. Loop until verified. Transform tasks into verifiable goals:

- "Add validation" → "Write tests for invalid inputs, then make them pass"
- "Fix the bug" → "Write a test that reproduces it, then make it pass"
- "Refactor X" → "Ensure tests pass before and after"

For multi-step tasks, state a brief plan:

```text
1. [Step] → verify: [check]
2. [Step] → verify: [check]
3. [Step] → verify: [check]
```

Strong success criteria allow independent looping. Weak criteria ("make it work") require constant
clarification.

**These guidelines are working if:** fewer unnecessary changes in diffs, fewer rewrites due to
overcomplication, and clarifying questions arrive before implementation rather than after mistakes.
