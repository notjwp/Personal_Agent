---
name: running-eval-cycles
description: Use when changing anything that could move this agent's pass rate — prompt text in SOUL.md, reflect logic, tool return values, context caps, policy rules, model or effort settings — or when reading eval/runs traces to decide what to fix next.
---

# Running Eval Cycles

## Overview

The pass rate is this project's headline number. `CONTEXT.md` §9 Step 4 constrains how it is moved,
because an unconstrained tuning loop rots: changes accumulate that nobody can attribute, and the
number stops meaning anything.

**Core principle: a change earns its place by moving the number, not by seeming right.**

## The Iron Law

```text
ONE CHANGE PER CYCLE. MEASURE. KEEP OR REVERT.
```

Two changes in one cycle and the delta cannot be attributed to either. That is not a style
preference — it destroys the only evidence the project has.

## The Cycle

1. **Read the traces** in `eval/runs/<timestamp>/`. Do not guess at causes.
2. **Classify every failure into exactly one bucket** (table below).
3. **Fix only the largest bucket.** Not the easiest, not the most interesting.
4. **Re-run the baseline: 3 runs per case.** A case passing 1 of 3 is not a passing case.
5. **Record the delta.** Keep or revert.
6. **Log the cycle** in `eval/CHANGELOG.md`: hypothesis, change, before, after, kept or reverted.

## Failure Buckets

| Symptom in trace | Bucket | Fix |
|---|---|---|
| One tool_result dominates the transcript | context flood | FR-401/402 — caps, spill |
| Same argument hash 3+ times in a row | thrashing | FR-106 — repeat detection |
| `stuck` at max_turns, no repeats | no strategy | plan node |
| Edits a file it never read | blind editing | prompt (`prompts/SOUL.md`) |
| Runs pytest once, never re-runs | no verify loop | prompt (`prompts/SOUL.md`) |
| `done` with the test still failing | termination bug | reflect logic |
| Tool exception repeated verbatim | bad error text | tool return value |

## Rationalizations

| Excuse | Reality |
|--------|---------|
| "It's neutral but it's cleaner, I'll keep it" | Revert it. Keeping neutral changes is exactly how the loop rots. |
| "These two changes are unrelated, batching is fine" | Then run two cycles. Unrelated changes still confound the delta. |
| "It's only a prompt tweak, not code" | Prompt changes are changes and are measured like code changes. |
| "One run is enough to see the direction" | Run-to-run variance on this task is large. 3 runs or it didn't happen. |
| "The held-out traces would tell me why this failed" | Looking at held-out traces during tuning converts them into dev cases. |
| "I'll log the cycle after a few more changes" | The log is the attribution record. Unlogged cycle = unattributable change. |
| "The fix is obvious from the failure, no need to bucket" | Bucketing is what stops you fixing the third-largest problem first. |

## Red Flags — STOP

- About to commit two behavioural changes together
- Reporting a pass rate from a single run
- Opening a `split: heldout` trace during a tuning cycle
- Keeping a change whose measured delta was zero
- Writing a fix before reading the traces
- `eval/CHANGELOG.md` has no row for the change you just made

**All of these mean: stop, revert to a clean baseline, run one change.**

## Milestones Only

Run the held-out 10 at milestones, never during tuning. Five dev cases means one flip is a 20%
swing; tuning against five overfits to five. A held-out score well below the dev score is
information — it tells you which fixes were general.

## Scope Guard

If a §9 step overruns its estimate by more than double, stop and reduce scope rather than pushing
through.
