# personal-agent

A single-user autonomous agent: goal in natural language, then a tool-calling loop with a policy
gate, bounded context, and checkpointing, until a terminal verdict.

Success is measured, not asserted. `eval/harness.py` runs a suite of deliberately broken practice
projects and scores each by exit code.

## Numbers

**Baseline — 2026-08-18.** Every dev case, three runs each, one configuration. This is the row every
later change is measured against.

| Date | Provider / model | Runs | Result | Blocked | Median tokens/run | Wall |
|---|---|---|---|---|---|---|
| 2026-08-18 | NVIDIA NIM / `meta/llama-3.1-70b-instruct` | 3 per case | **4/15** | 0 | 3,266 | 30 min |

| Case | Pass | Verdicts | Turns | Tokens (med) | Reads | Tampered |
|---|---|---|---|---|---|---|
| `missing-dep` | **3/3** | done x3 | 2/2/2 | 3,075 | 0 | 0 |
| `fix-import` | 1/3 | done x3 | 6/4/7 | 10,481 | 1/1/2 | 2 |
| `add-endpoint` | 0/3 | done x3 | 4/2/1 | 3,266 | 0 | 3 |
| `broken-fixture` | 0/3 | done x3 | 1/1/1 | 2,278 | 0 | 0 |
| `off-by-one` | 0/3 | done x3 | 7/7/9 | 19,058 | 2/2/3 | 0 |

All four passes are legitimate — zero tampering on every one. Every case fails for the exact reason
it was designed to, verified against the recorded intended failures after the run.

**The headline is not 4/15, it is `done x15`.** Not one run ended `stuck`, `compact` or `replan`.
The agent always believes it has succeeded, and is wrong 11 times out of 15. Two consequences:

- Neither compaction nor a plan node is earned by this data. The build spec predicted compaction
  next; the measurement says the termination check in `reflect` is the largest bucket by far.
- **Low token counts are not efficiency.** A 3,266-token median against a 60,000 target looks
  excellent and is not — runs stop early because the agent quits, not because it is frugal.

Two behaviours dominate the failures:

- **9 of 15 runs never called `read_file` at all.** All three `add-endpoint` runs open with
  `write_file` before reading anything.
- **5 runs edited the tests they are judged by.** The harness restores protected test files before
  scoring, so this cannot manufacture a pass — it shows up as wasted turns instead.

`broken-fixture` is the sharpest illustration: one `run_shell`, then `done`, in all three runs. It
runs the suite, sees the error, and declares victory without touching anything.

This baseline describes `meta/llama-3.1-70b-instruct`, not Claude. Tool-calling competence varies
enormously between models, so the number will move when the provider changes.

### Tuning: cycle 1, reverted

The first tuning cycle gated `done` on the most recent shell command having exited 0, aimed at the
`done x15` finding. It worked mechanically — turns roughly doubled and `stuck` verdicts appeared for
the first time — and it was **reverted** because the score got worse: 0 passes in 5 scored runs, and
tampering rose from 5/15 to 5 of 5.

The reason is the useful part. **The gate is satisfiable by tampering**: one run reached `done`
legitimately under the new rule by editing the test file until `pytest` exited 0. Denying the agent
its exit without giving it a better route to progress simply bought more of the destructive
behaviour it already knew.

So the binding constraint is **blind editing**, not termination. Recorded in full, including its
partial-measurement caveat, in `eval/CHANGELOG.md`.

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
