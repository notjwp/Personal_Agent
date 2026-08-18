# personal-agent

A single-user autonomous agent: goal in natural language, then a tool-calling loop with a policy
gate, bounded context, and checkpointing, until a terminal verdict.

Success is measured, not asserted. `eval/harness.py` runs a suite of deliberately broken practice
projects and scores each by exit code.

## Numbers

**No baseline has been committed yet.** A baseline is 3 runs per case; the row below is a single
run and is provisional.

| Date | Provider / model | Runs | Result | Median tokens | Note |
|---|---|---|---|---|---|
| 2026-08-18 | NVIDIA NIM / `meta/llama-3.1-70b-instruct` | 1 | **1/5** | 3,105 | First live loop. Phase B exit criterion met. |

Per case, that run:

| Case | Result | Verdict | Turns | Tools | Reads | Tampered | Tokens |
|---|---|---|---|---|---|---|---|
| `missing-dep` | **PASS** | done | 2 | 2 | 0 | 0 | 3,105 |
| `add-endpoint` | FAIL | done | 1 | 1 | 0 | 1 | 1,885 |
| `broken-fixture` | FAIL | done | 1 | 1 | 0 | 0 | 2,279 |
| `fix-import` | FAIL | done | 3 | 3 | 2 | 0 | 5,320 |
| `off-by-one` | FAIL | done | 8 | 8 | 2 | 0 | 18,264 |

1/5 is squarely inside the range the build spec predicts for a first result, and is not a problem.

**Read the token figures with suspicion.** A 3,105-token median is far under the 60,000 target, but
that is because runs terminate almost immediately, not because they are efficient. Every one of the
five ended with verdict `done`, four of them wrongly.

## Running it

```bash
docker build -f Containerfile -t personal-agent .
cp .env.example .env          # then add a free key from build.nvidia.com

docker run --rm --network none -v "$PWD:/app" personal-agent pytest -q   # 97 offline tests
python eval/harness.py --split dev                                       # scored run
python eval/harness.py --case fix-import --runs 3                        # one case, repeated
```

Offline tests need no API key and no network. Only the scored run calls a model.
