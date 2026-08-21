# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Repository state

Phases A-E and K-M are built. What exists: the measurement rig, the
`act -> gate -> execute -> reflect` loop, a two-provider model adapter, the interactive CLI with
approval and resume, kernel-enforced sandboxing, and a committed baseline.

- **Baseline: 14/15**, 3 runs per dev case, 0 blocked, on `nvidia/nemotron-3-super-120b-a12b`.
  Per-case table in `README.md`; conditions and trust checks in `eval/CHANGELOG.md`.
- **225 unit tests**, green with no API key, no network, a read-only root filesystem, and
  without the `mcp` package installed.
- **The model was the constraint, not the loop.** An earlier baseline of 4/15 on
  `llama-3.1-70b` was diagnosed as loop defects - 9 of 15 runs never called `read_file`, all 15
  ended `done` (11 wrongly), 5 rewrote their own tests. **All three vanished on a stronger model
  from the same free key.** One tuning cycle was spent and reverted before that was tried. When a
  number looks structurally wrong here, **probe a different model before tuning the loop** - 102 are
  available on the existing key.
- Still deferred: compaction, plan node, memory, web, worker. Real repositories produce
  `compact 14, done 9, stuck 7`, which looks like it earns compaction — **it does not**. A budget
  experiment tested exactly that and refuted it: given 1M tokens the agent used 281-516k and made
  *less* progress. The `compact` verdicts are a symptom of the write problem above, not a cause.
- **Held out: 29/30** on ten cases never seen during development (matched six 17/18, harder four
  12/12) - so the dev score was not overfitted.
- **Egress is restricted on scored runs.** The agent container has no route off the machine except
  an allowlisting proxy; the harness refuses to score a split without it. **Definition of Done: 9/9.**
- **Two tool changes took real repositories from 0/18 to `rich` 3/3, `click` 3/3, `cachetools` 2/3.**
  (1) `edit_file(path, old_string, new_string)`, exact and unique - a whole-file write could not fit
  a 2,689-line file into a 16,000-token reply. (2) `read_file` sizes its own window, so a paged read
  returns **164 contiguous lines instead of 30 head + 20 tail with the middle elided** - 17 reads to
  see a file rather than 54. Read:write went **1:29 -> 1:7**.
  **Do not quote a set-level percentage.** Three of six cases are measured, and they are the three
  that were tuned against; `real-markdown` and `real-more-itertools` have never run with either tool.
  The gain is also one case - strip `real-rich` out and it is +1 run and -1 run.
- **Ambiguity in edits was a SYMPTOM, not a cause.** A cycle that named the line numbers of each
  duplicate match moved 0/3 -> 0/3 and was reverted. The same failures vanished on their own once the
  agent could see contiguous code: an agent that can read a region picks a unique snippet by itself.
  Fuzzy matching is still unearned, and this is why.
- **Never wrap the harness in `timeout`.** It kills the client but leaves the container running; the
  orphan then corrupts the shared `eval/workspace` mid-way through the next case. Three runs were
  invalidated this way. `await_exclusive_workspace()` now blocks it, and `--continue` is the
  supported way to manage a long run. Twin of the `tail` lesson.
- **The 0/18 baseline that preceded it, and its cause.**
  `write_file` replaces a file entirely, so a five-line fix means emitting the whole file inside
  `MAX_TOKENS` (16,000, covering thinking + text + tool arguments). Real files are 559-2,689 lines:
  `rich/console.py` needs ~25,308 tokens to rewrite, **158% of one reply — that case is impossible,
  not merely hard.** Across 30 real-repo runs: **11 writes against 352 reads (1:32)**, nine runs
  ended `stop_reason: "length"`, and every run that made progress made exactly ONE write.
  **The fix is an edit tool** (`old_string` -> `new_string`, exact and unique). v1's "these files are
  30-80 lines" premise is dead; say so rather than reinterpreting it. Hermes solves this with a V4A
  patch format — port the idea, not the machinery: fuzzy matching is earned by evidence, not assumed.
- **Raising `BUDGET_TOKENS` does not raise `MAX_TOKENS`.** A budget experiment moved the per-RUN
  budget to 1M and changed nothing, because the wall is the per-REPLY cap. Check which limit binds
  before spending quota on the other.
- **The search for a harder case shape is over: three axes tried, three rejected.** Misdirection
  (held-out four, 12/12), cross-cutting edits (`pilot-crosscut`, 3/3), and independent bug count
  (`multibug`, **25/26** at 3–5 bugs) all failed the 40–70% band fixed in advance. Every set this
  project owns is saturated, so no tuning cycle can be measured **on the synthetic sets**; the
  real-repository split is where cycles now run.
  What the multibug set did yield is a rule: **each extra bug costs about 2.8 turns** (12.8 → 14.6 →
  18.4 mean, at 3 → 4 → 5 bugs), so difficulty on this axis is a budget question, not a reasoning
  one. The honest next step is a real repository and a real goal, not a fourth synthetic axis.
- **A 30-run scoring pass costs ~1.1M tokens and saturates the free tier for the day.** Budget one
  scored run per day on this key; the tier then rejects ~2 of 3 requests, which makes a 17-turn run
  complete with probability under 1%.

- **Two writable roots, and `--read-only` does not give you them (Phase K).** `/workspace` and
  `/state` (the agent home: checkpoints now, memory and skills later). The project tree is mounted
  `:ro`, because `--read-only` makes the ROOT FILESYSTEM immutable and **leaves bind mounts
  untouched** - so until K the agent could write to the harness, `tasks.jsonl` and the fixtures
  scoring it. No trace ever did, but the tamper check *repairs* where the kernel *prevents*, and a
  successful write to `/app` would not even have counted as a violation. Scored case-runs get a
  **blank** agent home; the interactive CLI keeps a persistent one.

- **MCP is in, and it bought efficiency rather than capability (Phase L).** One server
  (`mcp-server-fetch`, one tool), baked into the image at build time, stdio, running as a subprocess
  so Phase K's boundary already contains it. Same web split, one flag apart: **18/18 both ways**, but
  turns 6.2 -> 3.1 and median tokens 11,528 -> 7,293. `AGENT_MCP=off` restores the four-tool agent
  exactly, and every row records `mcp` and `schema_chars`.
- **`registry.py` still does not exist, and that is deliberate.** §12's trigger is "add at tool six";
  four built-ins plus `fetch` is five. The merge is nine lines in `tools.toolset()`. A deferred layer
  with a numeric trigger does not fire because the phase that would use it turned up.

- **Memory is in, and it is the project's cleanest result (Phase M).** Episodes in SQLite FTS5 at
  `/state/memory.db`, a profile at `/state/AGENT.md` written by the agent through `remember`.
  Recall split **0/18 -> 15/18**, and **40% CHEAPER** - it was budgeted as a cost and came out a
  saving, because an agent that remembers stops thrashing (7 `stuck` verdicts -> 0). `AGENT_MEMORY=off`
  restores the pre-memory agent exactly. Dev suite unmoved at 14/15.
- **`registry.py` exists but the `@tool` decorator still does not.** §12's "tool six" trigger
  fired; the decorator's own arithmetic did not. Five of six schemas are hand-written (fetch's
  comes from the server), break-even is above eight, and **the descriptions are load-bearing** -
  `edit_file`'s text coaches the model and is what took real repos 0/9 -> 4/7.

### Standing lessons, each paid for once

- **Tool schemas are rent, charged per turn, and this provider caches nothing.** `cache_read_tokens`
  was 0 on all 15 rows of a scored run, so the four built-in schemas were already ~23% of a median
  run. At a real MCP server's ~254 tokens/tool, 24 exposed tools would breach NFR-402 on schema
  ALONE. **Measure whether the provider caches before assuming tool breadth is cheap**;
  `MAX_SCHEMA_CHARS` now refuses a run that exceeds the budget.
- **A "derivable zero" baseline must still be measured.** The web split was supposed to score 0
  without a fetch tool. It scored **18/18**: `run_shell` plus Python's `urllib` already reaches the
  web, because the harness sets `HTTP_PROXY` in the container. Skipping that run as obvious would
  have shipped a false capability claim, unfalsifiable afterwards because the split would have
  looked like it went 0 -> 18.

- **A default that asserts the safe answer is how a row comes to claim a condition nobody checked.**
  `os.environ.get("AGENT_EGRESS", "restricted")` - and nothing anywhere set `AGENT_EGRESS`. Every
  trace row ever written asserted restricted egress, including runs deliberately made unrestricted.
  Where a fallback describes what was measured, it must say `UNKNOWN`.
- **A config file mounted into a container is read when the process starts, not when it changes.**
  tinyproxy refused a host that was already in its filter file, identically before and after SIGHUP.
  The mount propagated fine - that was checked separately, and it is the assumption that would
  otherwise have been blamed. Recreate the container; verify the effect, never the mechanism.
- **A declared JSON schema is not enforcement.** Numeric tool arguments arrive as strings; coerce at
  the tool boundary. This broke a live session twice before it was fixed.
- **Infrastructure failure is not a score.** A rate-limited run must be excluded and retried, never
  counted as a failed case.
- **Never pipe the harness through `tail`** - it buffers until exit, so a hang is indistinguishable
  from progress.
- **Verify the rig before believing a number**, in both directions: that passes are untampered, and
  that untouched fixtures still fail.
- **A blocked connection is not proof of a boundary.** "Could not resolve host" is DNS failing;
  re-test by raw IP before believing egress is closed.
- **When a fixture-era design decision meets real code, re-check its stated premise.** Three v1
  decisions were justified by numbers that only held for 10-file practice projects: whole-file writes
  ("these files are 30-80 lines"), the character caps in `shrink()` (short lines), and the scored
  check having no timeout (fast, terminating suites). All three broke on real repositories. The
  decisions were not wrong when made - their premises expired.
- **One surprising result is a hypothesis, never a finding.** A pilot case scored 1/3 and was written
  up as "the first genuine capability limit", with a tuning hypothesis built on it. The same case,
  unchanged, scored 3/3 in the full run. Both claims had to be retracted. This is the twin of the
  model-swap lesson above: probe before theorising, and repeat before believing.
- **"Running" is not "usable".** The egress proxy reported `State.Running: true` for two hours while
  failing every request: Docker's embedded DNS answered the A-only `getent hosts` but returned
  nothing for the dual-family `getent ahosts` that tinyproxy actually calls. A preflight must probe
  the operation the dependent code performs, not the one that is easy to check.
- **Text files crossing an OS boundary need their line endings pinned.** A config written on Windows
  and parsed by a Linux container fails on the trailing carriage return. Use newline="" when writing it.

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
pytest                            # 160 unit tests, no API key, no network
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
