# personal-agent

A single-user autonomous agent: goal in natural language, then a tool-calling loop with a policy
gate, bounded context, and checkpointing, until a terminal verdict.

Success is measured, not asserted. `eval/harness.py` runs a suite of deliberately broken practice
projects and scores each by exit code.

## Numbers

**Baseline — 2026-08-19, `nemotron-3-super-120b-a12b`: pass 14/15**, 3 runs per dev case, 0 blocked.

| Case | Pass | Verdicts | Turns | Tokens (med) | Tampered |
|---|---|---|---|---|---|
| `fix-import` | **3/3** | done x2 stuck x1 | 10/12/10 | 34,394 | 0 |
| `off-by-one` | **3/3** | done x3 | 10/9/8 | 28,877 | 0 |
| `broken-fixture` | **3/3** | done x3 | 8/10/10 | 27,464 | 0 |
| `missing-dep` | **3/3** | done x3 | 5/4/3 | 6,971 | 0 |
| `add-endpoint` | 2/3 | done x2 stuck x1 | 11/12/11 | 35,648 | 0 |

Ceilings: median 27,852 / 60,000 tokens, largest single result 2,908 / 6,000 chars — both inside.

**Verified before being believed:** zero tampering on any pass, zero attempted writes outside the
workspace, one model across all 15 rows, and every fixture re-checked to still fail exit-1 when
untouched. A passing suite proves nothing if the reset quietly stopped working.

**Two runs ended `stuck` and passed anyway.** Scoring is by the check command's exit code, never by
the agent's claim of success — so a correct fix counts even when the agent does not realise it has
finished. Had the verdict gated the score, those two would have been recorded as failures.

### The previous baseline, and what it actually measured

| Date | Model | Result |
|---|---|---|
| 2026-08-19 | `nemotron-3-super-120b-a12b` | **14/15** |
| 2026-08-18 | `meta/llama-3.1-70b-instruct` | 4/15 |

Only the model changed. Same harness, same fixtures, same loop — so **none of the improvement is
attributable to agent design**, and the two rows are separate measurements rather than a delta.

The older run's failures were bucketed at the time as loop defects: 9 of 15 runs never called
`read_file`, all 15 ended `done` (11 wrongly), and 5 rewrote the tests they were judged by. All
three disappeared on a stronger model. **That diagnosis was wrong**, and one tuning cycle was spent
and reverted before the cheapest alternative — a different model on the same free key — was tried.

**Read the token figures the right way round.** The old baseline's 3,266-token median looked
frugal and was not; runs were cheap because the agent quit early. 27,852 tokens with 8–12 turns is
what doing the work actually costs.

## Running it

```bash
docker build -f Containerfile -t personal-agent .
cp .env.example .env          # then add a free key from build.nvidia.com

# 150 offline tests - no API key, no network
docker run --rm --network none -v "$PWD:/app" personal-agent pytest -q

# a baseline: every dev case, three times, pacing between runs
python eval/harness.py --split dev --runs 3 --pace 20

# resume it if the provider refuses or you interrupt it
python eval/harness.py --split dev --runs 3 --pace 20 --continue

# one case, repeated
python eval/harness.py --case fix-import --runs 3
```

Offline tests need no API key and no network. Only the scored run calls a model.

A run that never reached the model is reported as **blocked** and excluded from the
score rather than counted as a failure - `pass 4/13, 2 blocked`, never `pass 4/15`.
Blocked runs are retried, and `--continue` re-runs exactly the ones with no result.

### Interactive

```bash
docker run --rm -it -v "$PWD:/app" -v "$PWD/eval/workspace:/workspace"   --env-file .env personal-agent python -m agent "Fix the failing tests."

python -m agent --list              # past threads
python -m agent --resume <id>       # continue one
```

Destructive commands pause for approval and show the full argument set; a task's
identity is its thread id, so resuming is re-invocation rather than a restart.
