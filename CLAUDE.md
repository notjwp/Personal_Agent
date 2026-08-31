# CLAUDE.md

Guidance for Claude Code working in this repository.

**This file is injected into every prompt, so it holds only what changes a decision.** Phase
narratives, cycle results and the reasoning behind each number live in `eval/CHANGELOG.md` (one
section per cycle), `ROADMAP.md` (per-phase plans and outcomes) and `README.md` (the numbers
table). Read those when you need history; do not copy history back into here.

## State

`act -> gate -> execute -> reflect` over a two-provider adapter, kernel-enforced sandbox, CLI and
Textual TUI, task queue, web search, measurement rig. **510 offline tests**, green with no API key, no network, a
read-only root filesystem, and without the `mcp` package installed.

| | |
|---|---|
| dev baseline | **15/15**, 3 runs per case, `nvidia/nemotron-3-super-120b-a12b` |
| held out | **29/30** — the dev score was not overfitted |
| real repositories | **4/10**; `real-humanize` 1 pass in 9 runs across four configurations |
| Definition of Done | **9/9** · must-have requirements **35/35** |
| search split | **9/9** with `web_search`, **0/9** with it removed |
| still unbuilt | streaming |

## Standing lessons, each paid for once

Ordered by how often they have caught something.

**Measurement**

- **One surprising result is a hypothesis, never a finding.** A pilot case scored 1/3, was written
  up as a capability limit, and scored 3/3 unchanged in the full run. Repeat before believing.
- **Probe a different model before tuning the loop.** A 4/15 baseline was diagnosed as three loop
  defects; all three vanished on a stronger model from the same free key, after a tuning cycle had
  been spent and reverted.
- **A "derivable zero" baseline must still be measured.** The web split was expected to score 0
  without a fetch tool and scored 18/18 — `run_shell` plus `urllib` already reached the web.
- **Infrastructure failure is not a score.** A rate-limited run is excluded and retried, never
  counted as a failed case.
- **Verify the rig in BOTH directions**: that passes are untampered, and that untouched fixtures
  still fail.
- **A fixture must not contain its own answer, and two-way verification will not catch it.** Verify
  three ways: untouched fails, a plausible answer *without* the knowledge fails, the correct answer
  passes.
- **Do not quote a set-level percentage when only part of the set is measured.** "4/10" rests on
  four of six real cases, and they are the ones that were tuned against.
- **A pass rate is not evidence for a mechanism that did not fire.** Check the instrumentation says
  the thing ran before attributing anything to it.

**The loop and the model**

- **Fixing one premature ending reveals the next.** Truncation ended runs at ~12 turns;
  fixing it exposed `MAX_SECONDS`; raising that exposed `BUDGET_TOKENS` at ~20 turns.
  Before raising a cap, check whether the run is starved or just spending badly - one
  spent 421,265 tokens to make a single edit.
- **A truncated reply is not a finished one.** `stop_reason == "length"` means the model
  ran out of output budget mid-sentence. Measured: 8 of 8 runs ending that way were
  scored `done`, and none passed. Any terminal check that ignores `stop_reason` will
  score a cut-off reply as success.
- **A cap sized against one failure mode outlives it.** `MAX_SECONDS` was set for a
  hanging tool; tools take 9s of a 950s run and it was ending working runs instead.
  Re-derive a cap when the thing it bounds changes shape.

- **Deterministic injection works; agent choice does not.** `write_episode` → `context_for` needed
  no decision and went 0/18 → 15/18. The `learn` tool needed one and was called 0 times in 15
  sessions. Prefer a rule over a request.
- **On this provider a tool-call history keeps producing tool calls** whether or not a tool is
  offered — proven with `tools` absent from the payload entirely. Neither an instruction nor an
  absence stops it. This is why planning as a *phase* failed: a phase inherits the message list.
- **A declared JSON schema is not enforcement.** Numeric arguments arrive as strings; coerce at the
  tool boundary.
- **A tool the model cannot see is a function, not a capability.** `run_python` and its tests were
  green with no `TOOLS` entry. Check the live toolset, not the suite.
- **Descriptions are load-bearing.** `edit_file`'s wording took real repositories 0/9 → 4/7. Never
  derive them from parameter names.
- **Check which limit binds before spending quota on the other.** Raising `BUDGET_TOKENS` (per run)
  changed nothing because the wall was `MAX_TOKENS` (per reply).
- **Compaction is not earned.** `compact` verdicts look like starvation; a budget experiment gave
  the agent 1M tokens, it used 281–516k and made *less* progress. The verdicts were a symptom of
  the write problem, not a cause.
- **Ambiguity in edits was a symptom, not a cause.** Naming duplicate-match line numbers moved
  0/3 → 0/3 and was reverted; the failures vanished once the agent could read contiguous code.
  Fuzzy matching stays unearned.
- **Each extra independent bug costs ~2.8 turns.** Difficulty on that axis is a budget question,
  not a reasoning one.

**Cost**

- **Tool schemas are rent, charged per turn, and this provider caches nothing.**
  `cache_read_tokens` is 0 across all 335 recorded rows. At 3,518 chars, definitions are already
  ~58% of a median run's billed tokens. `MAX_SCHEMA_CHARS = 10,000` is derived, not chosen: it is
  the largest cap at which NFR-402's median still holds. Measure whether a provider caches before
  assuming tool breadth is cheap.
- **The ~1.1M/day ceiling was wrong.** 3,677,100 scored tokens in one day with no throttle
  observed. Budget by wall-clock and blocked-run rate instead - the binding limit is that
  a single `real-*` run takes 10-20 minutes and can block on `APITimeoutError`.
- **A 30-run scored pass costs ~1.1M tokens and saturates the free tier for the day.** Budget one
  scored run per day; after that the tier rejects ~2 of 3 requests.

**Rig and environment**

- **`git revert --no-commit` leaves the revert STAGED, and `git checkout` carries it.**
  A docs commit that said "no code in this commit" silently reverted two cycles and
  invalidated the experiment comparing them - both arms were controls. The tell was a
  test count dropping 500 to 490, explained away instead of checked. Verify the arms
  DIFFER before running a controlled comparison.

- **One search endpoint is a rate limit wearing a capability's costume.** `html.duckduckgo.com`
  returns results once, then blocks, and every retry re-arms a ~30s cooldown - so an in-tool
  retry makes it strictly worse. Fanning out across engines fixed it with no retry and no
  sleep: 6 of 6 back-to-back searches returned results. Declare every host the library may
  dial, not the one you hoped it would.

- **Never pipe the harness through `tail`**, and never wrap it in `timeout`. `tail` buffers until
  exit so a hang looks like progress; `timeout` kills the client but leaves the container running,
  and the orphan corrupts the shared workspace mid-case. Three runs were lost that way. Use
  `--continue`.
- **A default that asserts the safe answer is how a row comes to claim a condition nobody checked.**
  `AGENT_EGRESS` defaulted to `"restricted"` and nothing ever set it, so every trace row ever
  written claimed restricted egress. Where a fallback describes what was measured, it says
  `UNKNOWN`.
- **A blocked connection is not proof of a boundary.** "Could not resolve host" is DNS failing;
  re-test by raw IP.
- **"Running" is not "usable".** A proxy reported `Running: true` for two hours while failing every
  request. A preflight must probe the operation the dependent code performs.
- **`--read-only` makes the ROOT FILESYSTEM immutable and leaves bind mounts untouched.** That is
  why the project tree is mounted `:ro` separately.
- **A config file mounted into a container is read when the process starts, not when it changes.**
  Recreate the container; verify the effect, never the mechanism.
- **Text files crossing an OS boundary need their line endings pinned** — use `newline=""`.
- **When a fixture-era design decision meets real code, re-check its stated premise.** Whole-file
  writes, `shrink()`'s character caps and the untimed check were all sound for 10-file practice
  projects and all broke on real repositories. The decisions were not wrong when made; their
  premises expired.

## CONTEXT.md is the authority

The binding build specification, not background reading. Its own precedence rules:

- **§9 is the only build order.** §5–7 describe the *finished* system and must not be implemented
  top to bottom. `[S]`/`[C]` items are out of scope until the `[M]` set passes evaluation.
- **§12 is a file allowlist.** Create the files it lists and no others; a deferred file is created
  only when its stated trigger fires. Every deviation so far is justified in writing inside §12
  itself — hold a new one to that standard.
- **§13 (Code Economy) is binding.** A violation is a defect, not a style choice.
- **Where §3/§4 and §13 disagree, §13 governs.** §3 is the logical flow; §13 is the code shape.
- **When a requirement and existing code disagree, the requirement wins** — say so rather than
  silently reinterpreting it. §8.2 records conflicts between requirements rather than papering
  over them.

## Architecture

Goal in natural language → tool-calling loop → terminal verdict. Three layers you must not
collapse:

- **Policy gate.** Every call is classified `auto`/`confirm`/`deny` *before* any side effect.
  `classify()` in `policy.py` is pure — no logging, no counters, no writes.
- **Context manager.** No tool output reaches the model unfiltered. `shrink()` caps each result,
  redacts secrets, spills the full text to `.agent/artifacts/`, and returns head + elision + tail +
  the path **plus instructions for reading it** — a bare path is ignored in practice.
- **Checkpointing.** State is written after every node transition, keyed by `thread_id`. A kill
  loses at most one node; resume is re-invocation with the same id.

**Only `act` calls a model.** Everything else is ordinary code and must be unit-testable without an
API key (NFR-602). This ratio is the most important design property in the system — resist pushing
logic into prompts.

**`gate` and `execute` must never merge** (CE-07). `gate` suspends on `interrupt()` and re-runs
from its first line on resume; merged, every already-executed tool fires twice. Nothing upstream of
a suspension point may have side effects (FR-305) — that applies to `adopt` as well as `gate`.

`config.py` is the single source of truth for the workspace root, model, caps and budgets. FR-302
and NFR-201 both depend on the workspace root having exactly one definition.

Prompts live in `prompts/` (`SOUL.md`, `PLAN.md`), version-controlled, never string literals
(NFR-603), read **inside** the node, never at import time (CE-05).

**Invariants that look wrong in isolation and are not** — §9 Step 2's corrections:
`START -> act`, not `START -> plan`. `done` is gated on whether any tool call was ever made, not on
a cursor. `failures` is a plain `int` counting *consecutive* failures, reset to 0 on a clean turn.
`RISK` is the single path through `classify()` — a name special-case would make the declaration
dead. State is a plain `TypedDict`: no reducers, no `Annotated`.

## Commands

```bash
python -m agent "goal"            # interactive; destructive calls pause for approval
python -m agent --tui             # Textual chat; --tui --resume <id> opens one thread
python -m agent --list            # past threads, newest first
python -m agent --resume <id>     # continue a thread; a task's identity IS its thread id

python -m agent --submit "goal"   # queue a task, print its id, return immediately
python -m agent --worker          # drain the queue; resumes anything a dead worker left
python -m agent --tasks           # queued / running / awaiting-approval / done / failed

python eval/harness.py --split dev --runs 3 --pace 20              # a baseline
python eval/harness.py --split dev --runs 3 --pace 20 --continue   # resume an interrupted one
python eval/harness.py --case fix-import --runs 3                  # one case, repeated

scripts/reset.sh <case-id>        # restore /workspace to a fixture's state (idempotent)
pytest                            # 510 tests, no API key, no network
```

Tests run in the container, which is the measured environment: read-only root, `--network none`,
`/workspace` and `/state` the only writable roots. The host Python is for editor resolution only.

A run that never reached the model is **blocked**, excluded from the score rather than counted as a
failure — `pass 4/13, 2 blocked`, never `pass 4/15`.

## Evaluation discipline

- **One change per cycle.** Two and the delta cannot be attributed.
- Prompt changes are changes and are measured like code changes.
- **Revert anything that does not move the number**, including changes that "seem right".
- Report pass counts across 3 seeds. 1-of-3 is not a passing case.
- Log every cycle in `eval/CHANGELOG.md`: hypothesis, change, before, after, kept or reverted.
- Do not read held-out traces while tuning.
- **A requirement is satisfied when the cases exercising it pass, not when the code exists.**

## Environment

Execution is confined to a container on any host that runs one (§11; NFR-701 as amended). This
machine is Windows 11 with Docker Desktop and Git Bash — not WSL2. `.agent/` and `hermes_copy/`
are gitignored.

## Say when it does not work

**Report failure plainly, in the first sentence, without softening.** "This did not work",
"the number did not move", "I got this wrong" — not "partially successful", not a win
reframed around a caveat. A capability that ships and is never used is a failure and gets
described as one: `learn` was called 0 times in 15 sessions, planning costs 30% more tokens
for no pass-rate gain, and both are recorded that way rather than as features.

**Unmeasured means unproven, and gets said out loud.** Code that exists is not a
requirement satisfied. A cycle is kept only when a number moved; anything that did not move
is reverted, including changes that "seem right". One favourable n=3 is a hypothesis - this
project has already retracted a 1/3 that re-measured at 3/3.

**Never quote a number better than the one measured.** No cherry-picking the better of two
identical runs, no set-level percentage when only part of the set ran, no attributing a pass
to a mechanism without checking the instrumentation says it fired.

## `hermes_copy/` is the reference implementation — consult it first

**Before building anything, look for how Hermes Agent solved it** (`hermes_copy/hermes-agent`,
127k lines, 21 plugin domains, gitignored and never shipped). It has been through more real
use than this project has, and three of its designs are already here — the compaction
boundary snap, the worker's transition/liveness pair, and the thrash detector's
idempotent/mutating split. Each was found by reading its code rather than reasoning from
scratch, and each was better than what this project had.

Three rules that make that safe rather than sloppy:

- **Take the DESIGN, port the code.** Every borrow so far was re-implemented against this
  project's shapes and was better for it: their `search_files` is ripgrep-backed and `rg` is
  not in the image; their tool sets are hand-kept frozensets where `policy.RISK` already
  classifies everything. Lifting verbatim would have imported dependencies and defects.
- **Do not create or edit `NOTICE` unless asked.** Hermes is MIT, so a borrow that copies
  its CODE carries an attribution obligation - raise that and let the user decide. A borrow
  that takes only the IDEA does not; note it at the site and move on.
- **A Hermes design still has to earn its place here.** §13 governs, and their fuzzy
  `edit_file` matching is a design this project measured (0/3 → 0/3) and reverted. Consult
  first, measure before keeping.

## Working guidelines

Behavioural rules that reduce common mistakes. These govern *how* to work; the sections above
govern *what* this project is.

**1. Think before coding.** State assumptions; if uncertain, ask. Present multiple interpretations
rather than picking silently. Say so when a simpler approach exists. If something is unclear, stop
and name what is confusing.

**2. Simplicity first.** Minimum code that solves the problem. No speculative features,
abstractions for single-use code, unrequested configurability, or error handling for impossible
scenarios. If 200 lines could be 50, rewrite it. Ask: *would a senior engineer call this
overcomplicated?*

**3. Comments earn their place, and the ceiling is three lines.** State *why*, never
*what* — the code says what. No comment longer than 3 lines; if the reasoning needs more,
it belongs in `eval/CHANGELOG.md` with a one-line pointer here. Do not narrate history in
source ("this was refuted", "the first version did X"), do not restate the requirement ID
and its text, and do not explain a decision the code makes obvious.

**EXCEPTION, and it is the only one: tool docstrings in `agent/tools.py` are not comments.**
`@tool` derives the JSON schema description from them, so the model reads them at runtime -
`edit_file`'s wording took real repositories 0/9 to 4/7. They are prompt text and are
governed by measurement, not by this rule.

**4. Surgical changes.** Touch only what you must. Do not "improve" adjacent code, comments or
formatting; do not refactor what is not broken; match existing style. Remove imports and variables
*your* change orphaned — mention pre-existing dead code rather than deleting it. **The test: every
changed line traces to the request.**

**5. Goal-driven execution.** Turn tasks into verifiable goals — "add validation" becomes "write
tests for invalid inputs, then make them pass". For multi-step work, state a brief plan with a
verification per step. Strong success criteria allow independent looping; weak ones ("make it
work") force constant clarification.
