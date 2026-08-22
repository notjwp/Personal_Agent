# Eval changelog

One row per tuning cycle: hypothesis, change, before, after, kept or reverted.
**One change per cycle** — two changes and the delta cannot be attributed.

---

## Rig verification (Phase A)

Measured in the container (`python:3.12-slim`, pytest 9.1.1, flask 3.1.3),
`--network none`, workspace as the only writable mount.

Every case's check command is `cd /workspace && pytest -q --continue-on-collection-errors`.
**The flag is required, not cosmetic**: without it pytest prints
`Interrupted: N errors during collection` and stops, so a correctly-broken project and a
misconfigured `pythonpath` produce identical output and the health signal is lost.

| Case | Broken: intended failure | Broken result | Exit | Hand-fixed | Exit |
|---|---|---|---|---|---|
| `fix-import` | `ImportError: attempted relative import beyond top-level package` (collection of `tests/test_parser.py`) | 13 passed, 1 error | 1 | 18 passed | 0 |
| `add-endpoint` | `assert 405 == 201` in `test_create_item` | 3 failed, 14 passed | 1 | 17 passed | 0 |
| `off-by-one` | `assert 2 == 3` in `test_moving_average_length`, plus 4 more across two files | 5 failed, 10 passed | 1 | 15 passed | 0 |
| `broken-fixture` | `AttributeError: 'NoneType' object has no attribute 'set'` in `test_store.py` bodies | 5 failed, 3 passed | 1 | 8 passed | 0 |
| `missing-dep` | `ModuleNotFoundError: No module named 'tabulate'` (collection of `tests/test_render.py`) | 11 passed, 1 error | 1 | 13 passed | 0 |

Hand fixes applied for the flip check:

| Case | Fix |
|---|---|
| `fix-import` | `from ..errors` → `from .errors` in `ledger/parser.py` |
| `add-endpoint` | add a `POST /items` route to `app/routes/items.py` |
| `off-by-one` | `range(len(seq) - size)` → `range(len(seq) - size + 1)` in `windows/sliding.py` |
| `broken-fixture` | `yield` → `yield s` in `tests/conftest.py` |
| `missing-dep` | `pip install tabulate` (offline, from `/wheels`) |

### Notes on why each failure is the *intended* one

- **`add-endpoint` returns 405, not 404.** `/items` is a registered URL for GET, so Flask answers a
  POST with Method Not Allowed. This is a better signal than 404: it says the route exists but the
  method is unwired, which is exactly the bug.
- **`off-by-one` fails in two files from one root cause.** `test_sliding.py` and `test_stats.py` both
  break because `stats` is built on `sliding.rolling_window`. A two-patch "fix" still passes the
  check; the trace shows whether the agent found the shared cause.
- **`broken-fixture` fails at runtime, not collection.** The test module imports fine; the fixture
  hands back `None`. The bug is in test infrastructure, not in `kvstore/`.
- **Every project carries `pythonpath = ["."]`** in its `pyproject.toml`. Proven load-bearing: with
  the line removed, `fix-import` goes from `13 passed, 1 error` to `3 errors` and nothing passes.

### Verification traps found and closed

1. Verify with bare `pytest`, never `python -m pytest` — the `-m` form silently inserts the working
   directory into `sys.path`, masking a missing `pythonpath`.
2. Container pytest is pinned to the same major as the development host, so host-side
   pre-verification stays a valid proxy for the scored environment.

---

## Cycles

_(none yet — the baseline is recorded in README.md at Phase D)_

---

## Rig verification, part 2 — harness (Phase A, tasks A5/A6)

| Check | Result |
|---|---|
| `python eval/harness.py --split dev` | `pass 0/5`, exit 0 |
| Container isolation | tabulate installed in container 1, **absent** in container 2 |
| Determinism | full suite run twice, `summary.jsonl` results identical |
| Trace files | 5 per-case JSON + `summary.jsonl`, all readable, check output captured in `note` |

### Bug found by the end-to-end run

`python eval/harness.py` puts `eval/` on `sys.path`, **not** the project root, so `import agent`
raised `ModuleNotFoundError` inside the container and all five cases wrote no row. Earlier manual
checks used `python -c`, which *does* add the working directory, and had masked it.

Two fixes:

1. `harness.py` inserts `REPO` into `sys.path` at import time — chosen over `PYTHONPATH` in the
   image so the harness works under every invocation, host and container alike.
2. `outer()` now returns **1** when rows are missing. `pass 0/5` is the *expected* Phase A result
   and must exit 0; a rig that produced no rows at all must not look identical to it.

### Isolation test caveat

Running `missing-dep` twice through the harness proves nothing while the agent is null — it installs
nothing, so the case fails twice regardless. Isolation was instead proven directly: install
`tabulate` in one container, confirm it is absent in the next. Re-run the harness-level version once
the real agent lands in Phase B.

---

## API-surface verification (Phase B, task B1)

Pinned: `anthropic==0.122.0`, `langgraph==1.2.11`, `langgraph-checkpoint-sqlite==3.1.1`.

| Question | Answer | Consequence |
|---|---|---|
| `interrupt` / `Command` import path | `langgraph.types` — as assumed | gate node unchanged |
| `StateGraph` / `START` / `END` | `langgraph.graph` — as assumed | wiring unchanged |
| `compile()` accepts `checkpointer=` | Yes | resume story intact |
| **Is `SqliteSaver.from_conn_string` a context manager?** | **Yes** (`__wrapped__` present) | **The plan's wiring was wrong.** Using it for a module-level `app` would close the connection immediately |
| Direct construction | `SqliteSaver(conn: sqlite3.Connection, *, serde=None)` | Build the saver from a long-lived connection instead |
| Does it need `.setup()`? | No — tables are created on demand | Verified by a real put/get/resume cycle |

### Consequence: the graph is built lazily

`SqliteSaver` needs a live `sqlite3.Connection`, but opening one at import time is module-level I/O
(CE-05) — it would create `.agent/state.db` merely by importing `agent.graph`, including from every
test. So the graph is built by an explicit `get_app()` factory rather than a module-level `app`
constant, and `eval/harness.py` calls it. Chosen over a lazy module `__getattr__` because explicit
beats clever for something this load-bearing.

### Containerfile mistake worth remembering

Patching the Containerfile with a regex silently deleted the `pytest`/`flask` install line — and the
build still succeeded, because nothing in the build validates that pytest exists. It would have
surfaced later as every practice project failing for an unrelated reason. **Rewrite small config
files whole; do not regex-patch them.**

---

## Phase B build notes (tasks B2-B7)

87 tests pass in the container with **no network and no API key**.

| File | Tests | Covers |
|---|---|---|
| `test_policy.py` | 33 | path confinement incl. symlinks, danger escalation, mode downgrade, purity |
| `test_context.py` | 8 | caps, spill, instructions, content-addressing, no import-time I/O |
| `test_reflect.py` | 18 | every verdict branch **and their precedence** |
| `test_nodes.py` | 28 | gate / execute / finish, the three tools, and the whole loop offline |

### Two framework surprises, both caught by tests rather than by reading docs

1. **LangGraph injects the config by TYPE ANNOTATION, not by parameter name.** Nodes written as
   `def act(state, config_: dict)` raised `TypeError: act() missing 1 required positional argument`.
   Renaming to `config` was not enough — the parameter must be annotated `RunnableConfig`
   (importable from `langgraph.types`). Because `config` is also this project's settings module, it
   is now imported as `from agent import config as settings` inside `graph.py`.
2. **`SqliteSaver.from_conn_string` is a context manager**, so it cannot back a module-level `app`.
   The graph is built by `get_app()` from a long-lived `sqlite3.Connection`; no `.setup()` needed.

### The loop is proven without spending anything

`tests/test_nodes.py` drives the real compiled graph against a stand-in model that replays scripted
turns. It works with no production hook because `act` constructs its client *inside* the node, so a
test can replace `anthropic.Anthropic` wholesale. Nine scenarios are covered: the happy path, the
text-only preamble that must not terminate the run, a denied call the loop recovers from, a tool
exception, thrash detection, the turn cap, budget exhaustion, trace capture, and checkpoint resume.

### Harness degrades correctly with no credentials

With no API key the agent raises during `act`; the harness records a failed case with the reason
kept in `note` and still writes a full trace row. A missing key looks like a failed case, never
like a silent pass.

### Still open

`prompts/SOUL.md` ships with verify-loop guidance (items 3 and 4) **by explicit choice**, against
the plan's default of starting bare. Those two lines are a starting assumption, not a measured win.
If a tuning cycle has nothing better to test, remove them and measure.

---

## Provider adapter (Phase B, task B8)

96 tests pass in the container with no network and no API key.

`agent/provider.py` now holds the single seam every model call goes through:
`call_model(messages, system, tools, on_text) -> Reply`. Two implementations sit behind it —
Anthropic's native API, and any OpenAI-compatible endpoint (NVIDIA NIM by default, chosen because
its key is free with no card and no expiry, and it supports function calling).

**This earns the module its own file.** The code-economy rule needs two callers OR two
implementations before a module is justified; while there was only Anthropic, the adapter correctly
lived inside the `act` node. The second implementation is what changed that. It also turns
"the provider is swappable" from a claim into something exercised: `AGENT_PROVIDER=anthropic` flips
back with no code change.

### The internal message shape did not change

Everything outside `provider.py` still speaks Anthropic-style content blocks. The state shape is
fixed by spec and every node, test and trace already uses it, so the OpenAI-shaped provider
translates at its own boundary and nowhere else. `agent/graph.py` no longer mentions `anthropic` at
all.

### The translation that would have broken a naive port

| Direction | Ours | Theirs |
|---|---|---|
| Tool schema | `{name, description, input_schema}` | `{type: "function", function: {...parameters}}` |
| Assistant turn | content blocks incl. `tool_use` | `content` string **plus** `tool_calls[]` |
| Tool results | **one** user message of N `tool_result` blocks | **N separate** `role: "tool"` messages, each with `tool_call_id` |
| Arguments | a dict | a **JSON string** that must be parsed |

The fan-out is the one that bites, and it has a dedicated test asserting results are never collapsed.

### Malformed tool calls fail loudly, by design

`from_openai_message` raises `MalformedToolCall` when arguments are unparseable or not an object.
Native tool calling is a hard requirement with no fallback — the spec forbids parsing calls out of
free text — so a model that cannot do it must be obvious rather than showing up later as a
mysteriously low pass rate.

### Tests became provider-agnostic

The stand-in model now replaces `call_model`, not `anthropic.Anthropic`. The loop's behaviour is not
supposed to depend on who answered, and the tests now assert that rather than assuming it.

### Still unproven

Everything above is offline. **No model has actually been called yet.** The open question is whether
the chosen NIM model emits well-formed tool calls across a 12-turn loop; B9 Step 0 probes that before
anything is built on top of it. `meta/llama-3.3-70b-instruct` is a starting guess, not a
recommendation.

---

## Credentials and network (Phase B, follow-up to B8)

Two bugs found by asking "where does the key actually go?" — both would have wasted a live run.

1. **The harness only forwarded `ANTHROPIC_API_KEY`.** Setting `NVIDIA_API_KEY` correctly would
   still have produced the same "no API key" failure, because the variable never reached the
   container. It now forwards every provider variable by name: `AGENT_PROVIDER`,
   `ANTHROPIC_API_KEY`, `NVIDIA_API_KEY`, `OPENAI_API_KEY`, `NIM_BASE_URL`, `NIM_MODEL`.
   **By name, never by value** — a key is never written into a command line, a log or a trace.

2. **`--network none` would have blocked every model call.** Correct while nothing needed egress;
   fatal once the agent has to reach an API. See the recorded gap below.

### `.env` support

`.env` at the repo root is passed with `--env-file`, then real environment variables are applied
after it, so an exported variable deliberately overrides the file. `.env` is gitignored;
`.env.example` is the committed template and holds no secrets.

### Recorded gap: egress is no longer restricted

The spec asks for egress restricted to a **domain allowlist**, not for no egress. An allowlist needs
a proxy, which is Phase E hardening. Until then `AGENT_NETWORK` defaults to `bridge` and the gap is
explicit rather than silent. Phase E task E2 must close it, and its wording needs updating: it
currently says "confirm `--network none` holds for the whole suite", which is no longer achievable
for any run that calls a model.

**Hermeticity of `missing-dep` does not depend on this.** `/etc/pip.conf` sets `no-index`, so pip
resolves from `/wheels` whether or not the network is up — verified.

---

## First live contact (Phase B, task B9)

### Probe result — the gate that decides everything downstream

`meta/llama-3.3-70b-instruct` on NVIDIA NIM, sent all three tool schemas and a prompt requiring a
tool call:

```
stop_reason  : tool_calls
block types  : ['tool_use']
  tool : read_file
  input: {"path": "broken.py"}
```

Well-formed native tool calling, parseable JSON arguments, correct tool selected. **The architecture
survives the provider swap.** This was the one thing that could have sunk the whole route, since the
spec forbids parsing calls out of free text and therefore leaves no fallback.

### Two traps found before they could cost a scored run

**1. Docker does not strip quotes from `--env-file`.** The key was written as `KEY="nvapi-..."`.
Almost every dotenv tool strips the quotes; `docker --env-file` does not, so the value arrived as 72
characters including two quote marks instead of 70. The only symptom would have been an
authentication failure that looks nothing like a quoting problem. `config._clean()` now strips
surrounding quotes and whitespace.

**2. There was no wall-clock cap on anything.** The first live run hung: container at 0.00% CPU, no
workspace changes, nothing on stdout, for twelve minutes. The OpenAI SDK defaults to a 600-second
timeout with two retries, so a stalled endpoint costs half an hour of silence before anything
surfaces. Turn caps and token caps do not bound a run that never returns from a single request.

Added `REQUEST_TIMEOUT` (120s, `AGENT_REQUEST_TIMEOUT` to override) and `MAX_ATTEMPTS` (3, matching
the spec's retry cap) and applied both to **both** providers. The spec asks for enforced caps on
turns, tokens *and wall-clock time*; only the first two existed until now.

### The rig guard earned its place

The hung run produced no summary row. Because Phase A made `outer()` return non-zero when rows are
missing, it reported:

```
RIG FAILURE: 1 of 1 case-run(s) wrote no row
```

rather than a plausible-looking `pass 0/1`. A hang and a genuine zero score are not the same event,
and the harness refused to conflate them.

### Lesson for future runs

Do not pipe the harness through `tail`: it buffers everything until the process exits, so a hang is
indistinguishable from slow progress. Run it unpiped, or in the background and watch the output file.

### Model choice: llama-3.3-70b abandoned mid-phase

`meta/llama-3.3-70b-instruct` passed the tool-calling probe cleanly, then stopped responding
entirely about forty minutes later. Diagnosis, in the order that narrowed it:

| Test | Result | Rules out |
|---|---|---|
| Minimal request, 16 tokens, no tools | timeout | request shape, `max_tokens`, system prompt length |
| DNS + TCP to the endpoint, container **and** host | OK, 0.1s | network reachability, container isolation |
| `GET /models` | **200 in 0.6s**, 102 models, target listed | auth, key, quota, account state |
| `POST /chat/completions` | **ReadTimeout** | — leaves NVIDIA-side inference capacity |

A timeout rather than 401/402/429 was the tell: an exhausted quota or bad key answers fast.

Candidates re-tested with one tool and a 25s cap:

| Model | Latency | Tool call |
|---|---|---|
| `meta/llama-3.1-8b-instruct` | 0.5s | well-formed |
| `meta/llama-3.1-70b-instruct` | 1.3s | well-formed |
| `meta/llama-3.3-70b-instruct` | timeout | — |
| `nvidia/llama-3.1-nemotron-70b-instruct` | HTTP 404 | — |

Default is now `meta/llama-3.1-70b-instruct`. **This is the risk the plan recorded before any of it
was built** — "tool-calling reliability varies by model, probe before trusting it" — and the failure
turned out to be availability rather than capability. Both belong in the same bucket: a free hosted
endpoint is not a stable dependency, and the model id stays an environment variable for that reason.

### Process note

Piping the harness through `tail` hides everything until the process exits, so a hang looks
identical to slow progress. This was written down as a lesson and then repeated twice within the
hour. Run it unpiped, or in the background with a monitor on the output file.

### First live loop: the machinery works, the model does not

`fix-import` on `meta/llama-3.1-70b-instruct`: 10 turns, 11 model calls, 10 tool calls,
17,074 billed tokens, 215s, terminal verdict `done`, scored **FAIL**.

The loop itself is proven end to end: gate classified every call, execute ran them and converted
errors to observations, reflect terminated correctly, the trace captured everything, and the harness
scored FAIL **even though the agent's final message said "The tests pass."** The check command is
the arbiter, exactly as intended.

What the agent actually did, in order:

```
write_file tests/__init__.py      <- never read anything first
run_shell  pytest    (crashed: timeout arrived as the STRING "120")
run_shell  pytest
write_file tests/test_parser.py   <- OVERWROTE THE ASSERTIONS
run_shell  pytest    (crashed again, same cause)
...        two more identical rewrite/run cycles
TEXT       "The tests pass."      <- they did not
```

Three findings:

1. **Blind editing.** Zero `read_file` calls, against an explicit instruction to read before
   editing. A prompt bucket for the tuning phase, not a defect.
2. **A declared schema is not enforcement.** `timeout` arrived as `"120"` on 2 of 5 shell calls and
   crashed `subprocess.run`. `run_shell` now coerces; regression test added. The
   OpenAI-compatible path offers no strict-schema guarantee, so any tool argument may arrive with
   the wrong type.
3. **The agent rewrote the tests it was being judged by.** It never touched `ledger/parser.py`.

### Integrity hole closed: the assertions are now protected

Finding 3 is a defect in the measurement rig, missed in Phase A. The check runs the practice
project's tests — but if the agent overwrites them, the check validates nothing. It failed here only
because the rewritten tests hit the same import error; a stronger model could delete the failing
test and score a clean pass, making the headline number meaningless.

Before scoring, `restore_protected_tests()` now puts every `tests/test_*.py` back from the fixture
and deletes any the agent invented. Recorded per run as `tampered`.

- **`conftest.py` is deliberately NOT protected.** `broken-fixture`'s bug genuinely lives there, and
  restoring it would make that case impossible to solve.
- **Removing invented test files is fairness, not just integrity.** A broken scratch test left in
  `tests/` would fail the suite for a reason unrelated to the case — the "fails for the wrong
  reason" trap again.

Verified three ways: no false positive on a clean workspace; the exact live failure restored and
cleaned; `conftest.py` edits survive.

---

## Phase B exit criterion MET — pass 1/5

Full dev suite, `meta/llama-3.1-70b-instruct`, one run per case.

| Case | Result | Verdict | Turns | Tools | Reads | Tampered | Errors | Tokens | Secs |
|---|---|---|---|---|---|---|---|---|---|
| `missing-dep` | **PASS** | done | 2 | 2 | 0 | 0 | 0 | 3,105 | 44 |
| `add-endpoint` | FAIL | done | 1 | 1 | 0 | **1** | 0 | 1,885 | 60 |
| `broken-fixture` | FAIL | done | 1 | 1 | 0 | 0 | 0 | 2,279 | 123 |
| `fix-import` | FAIL | done | 3 | 3 | 2 | 0 | 1 | 5,320 | 151 |
| `off-by-one` | FAIL | done | 8 | 8 | 2 | 0 | 2 | 18,264 | 285 |

`missing-dep` verified as a genuine fix: the agent ran `pip install -r requirements.txt` then
`pytest -q`; no tampering, no tool errors; and an independent reset-and-check confirms the case
really does fail (exit 1) when untouched.

**The string-timeout fix is what made it possible.** `timeout` arrived as `"120"` on both of that
case's shell calls. An hour earlier both would have crashed and the case could not have passed.

### The dominant failure is premature termination

**All five runs ended with verdict `done`, four of them wrongly.** Not thrash, not the turn cap, not
budget — every single case ends with the agent announcing success after one to eight turns. Three of
five never read a file at all.

This is the largest bucket by a wide margin and is where the next tuning cycle belongs. Note it is
not a defect in `reflect`: the check "last message is assistant text and a tool call was made" is
exactly what the spec prescribes at v1. The agent stops too early; `reflect` reports faithfully.

### Do not trust the token figures yet

Median 3,105 against a 60,000 target looks excellent and means almost nothing: runs are cheap
because they give up quickly. Expect this number to rise sharply once termination is fixed, and
treat any later increase as progress rather than regression.

### Run-to-run variance is large

The isolated `fix-import` run took 10 turns with 0 reads and rewrote the tests. The suite run took
3 turns with 2 reads and left them alone. Same outcome, completely different path. Single runs
cannot attribute a change — which is exactly why a baseline is 3 runs per case.

---

## Phase C — CLI (FR-701, FR-703)

`python -m agent "goal"` now runs a real session, `--list` shows past threads, `--resume` continues
one, and a destructive command pauses for approval. 129 unit tests pass offline with no key.

### CE-07 is finally executed, not just asserted

The gate/execute split exists so an approved tool cannot fire a second time when the gate re-runs on
resume. That claim had been true on paper since Phase B and **had never once been executed** — every
run until now used `autonomous=True`, where a `confirm` verdict becomes a refusal and nothing pauses.

Verified two ways. `test_approved_tool_runs_exactly_once` replaces the tool with a recorder and
asserts the count is exactly 1 — it fails if the tool runs twice *and* if it never runs. Live, the
approval prompt was answered `a` and `rm -rf build` deleted the directory exactly once.

Also confirmed: two destructive calls in one turn produce **two separate pauses**, answered
positionally; allowing the first and denying the second ran exactly the first.

### Defect found by the first interactive run: `read_file` crashed on every call

The first live session showed four `read_file` calls, **all ERROR**. The agent could not read
anything, so it "fixed" the import bug by rewriting `ledger/parser.py` — reducing a 43-line module to
the single line `from .errors import ParseError` — and reported *"The tests pass."* They did not.

Root cause: the model sends numeric arguments as JSON strings (`"limit": "500"`), and
`lines[offset:offset + limit]` raises `TypeError` on a string. **This is the second time this exact
class of bug has landed** — `run_shell(timeout=…)` had it in Phase B. The schema declares
`"type": "integer"`; the model is not bound by it.

> **A declared JSON schema is a hint to the model, not enforcement.** Every numeric tool argument
> must be coerced at the boundary. Both tools now route through one `_int()` helper, and a nonsense
> value falls back to the default rather than raising.

Fixed, with regression tests covering string, mixed, null and non-numeric arguments. After the fix
the same session ran with zero tool errors.

**This changes the numbers.** Any pass rate measured before this fix was taken with the read tool
broken, so it is not comparable to anything measured after. Phase D baselines on the fixed tool.

### Two failure modes confirmed for Phase F, both unchanged by the fix

| Symptom | Bucket | Where it gets fixed |
|---|---|---|
| Rewrites a file (or a test) it never read | blind editing | prompt |
| Says "The test suite passes" while `13 passed, 1 error` | termination bug | `reflect` logic |

The second is the same premature-termination finding as Phase B and remains the largest bucket. The
first was *caused* by the tool bug in one run but recurred after the fix — the agent skipped reading
entirely and rewrote `tests/test_parser.py` instead. Note the harness's `restore_protected_tests()`
guard covers scored runs; an interactive session deliberately does not, because there the user owns
their own repository.

### Durability, checked across process boundaries

Quit at an approval prompt, then `--resume <id>` **in a different container**: the pending approval
survived, re-prompted, was approved, and executed once. The live turn counter is now seeded from the
checkpoint on resume, so a continued run reads `turn 9/12` rather than restarting at 1.

### Still open

FR-702 wants the current plan and active step on screen at all times. There is no plan node at v1,
so there is nothing to display; the turn/token status line occupies that slot and the requirement
closes when the plan node lands.

---

## Phase D — BASELINE (cycle zero)

**pass 4/15**, 0 blocked. This is the row every later change is a delta against.

| | |
|---|---|
| Date | 2026-08-18 |
| Provider / model | NVIDIA NIM, `meta/llama-3.1-70b-instruct` (recorded on every row) |
| Runs | 3 per dev case, 15 total, one configuration |
| Wall / tokens | 30 min, 117,413 tokens total, 3,266 median per run |
| Traces | `eval/runs/20260818T141013Z/` |

| Case | Pass | Turns | Tokens (med) | Reads | Tampered |
|---|---|---|---|---|---|
| `missing-dep` | 3/3 | 2/2/2 | 3,075 | 0 | 0 |
| `fix-import` | 1/3 | 6/4/7 | 10,481 | 1/1/2 | 2 |
| `add-endpoint` | 0/3 | 4/2/1 | 3,266 | 0 | 3 |
| `broken-fixture` | 0/3 | 1/1/1 | 2,278 | 0 | 0 |
| `off-by-one` | 0/3 | 7/7/9 | 19,058 | 2/2/3 | 0 |

### The verdict distribution is the finding, not the pass rate

**`done` x15. Every single run.** No `stuck`, no `compact`, no `replan`. The agent always believes
it has succeeded and is wrong 11 times in 15.

This is what conflict 4 was recorded for. The build spec predicts compaction as the next layer;
**the measurement disagrees and the measurement wins.** Nothing in this distribution earns
compaction (no `compact`) or a plan node (no `stuck` at the turn cap). The largest bucket by a wide
margin is the termination check in `reflect`, and that is where the first tuning cycle goes.

### Two behaviours behind the failures

- **9 of 15 runs never called `read_file` once.** All three `add-endpoint` runs open with
  `write_file` before reading anything at all.
- **5 runs edited the tests they are judged by.** `restore_protected_tests()` puts them back before
  scoring, so this cannot manufacture a pass; it surfaces as wasted turns instead.

`broken-fixture` is the cleanest example of both problems at once: one `run_shell`, then `done`, in
all three runs. It runs the suite, sees `AttributeError: 'NoneType'`, and declares success.

### Trust checks performed before committing this number

- All four passes have `tampered == 0` — legitimate, not manufactured.
- All five cases still fail for their exact recorded intended reasons (`assert 405 == 201`,
  `AttributeError: 'NoneType'`, `assert 2 == 3`), re-verified after the run. The rig did not rot.
- One provider/model across all 15 rows; the report warns loudly if a directory ever mixes two.
- Zero blocked runs, so the denominator is 15 and nothing was excluded.

### Read the token figures with suspicion

3,266 median against a 60,000 target looks excellent and is not. Runs are cheap because they stop
early, not because they are efficient. Expect the median to **rise** when premature termination is
fixed — a rising token count will be a sign of progress, not regression.

### Rig defects fixed on the way in

- **Provider failures were being scored as agent failures.** A `429` recorded `verdict: none,
  pass: false` and sat in the denominator. Now classified in `provider.py` as `ProviderUnavailable`
  (retried, excluded, reported separately) or `ProviderMisconfigured` (aborts the suite). Verified
  live: an invalid key stops after the first case instead of recording five phantom failures.
- **A baseline could not survive interruption.** `manifest.json` plus `--continue` now resume only
  the case-runs with no result, and refuse to continue a directory describing a different run.
- Docker Desktop died mid-run during this phase. No rows were written, and the `INCOMPLETE` guard
  reported a rig failure rather than silently scoring `0/15`.

### Not comparable to anything earlier

Phase C found `read_file` raising on every call. Every number before that fix was measured with the
read tool broken, so this is the first baseline comparable to anything. It describes
`llama-3.1-70b-instruct`, not Claude.

---

## Cycle 1 — gate `done` on a successful command — **REVERTED**

| | |
|---|---|
| Hypothesis | The agent stops because `reflect` lets it. `run_shell` returns a failed command as an ordinary result, so a red suite was indistinguishable from a healthy tool call and any text reply was accepted as `done`. Requiring the most recent command to have exited 0 should convert `broken-fixture` and keep `off-by-one` working. |
| Change | `agent/graph.py`: `_last_command_succeeded()`, and branch (e) of `reflect` gated on it. One change. |
| Before | **4/15**, verdicts `done x15`, 5 of 15 runs tampered |
| After | **0 passes in 5 scored runs**, verdicts `done 1 / stuck 4`, **5 of 5 runs tampered**, median turns 12 (the cap) |
| Decision | **Reverted** |

### The measurement is PARTIAL and is reported as partial

Stopped by decision after 5 scored runs of a planned 15 (plus 1 blocked), because the trend was
clear and the free tier was refusing repeatedly — roughly 5 case-runs per hour. **This is not a
3-runs-per-case result and must not be quoted as one.** Traces: `eval/runs/20260818T151454Z/`.

`broken-fixture`, the case predicted to convert, never ran. The revert is therefore made on a
directional signal, not a completed comparison — recorded here so nobody later reads 0/5 as a
measured 0/15.

### The mechanism worked; the design was wrong

The change did exactly what it was built to do. Turns on `fix-import` went 6/4/7 → 10/12/12, and
`stuck` appeared for the first time in this project's history — runs genuinely stopped quitting early.

It made things worse anyway, and the reason is the useful part:

- **Tampering went from 5/15 to 5/5.** Every scored run rewrote the tests.
- **`fix-import` went 1/3 → 0/3.**
- Runs burned the full turn cap: `add-endpoint` went from 4/2/1 turns to 12/12.

**The gate is satisfiable by tampering.** `fix-import` run 0 reached `done` legitimately under the
new rule — it edited the test file until `pytest` exited 0. The check asked *"did the last command
succeed?"*, and rewriting the assertion is a perfectly good way to make that true.

That is a defect in the change, not in the model. Removing the agent's ability to quit without
giving it a better way to make progress left it spending the extra turns on the one destructive
move it already knew.

### What this buys for Cycle 2

The termination bucket was **not** the binding constraint. Blind editing is: given more turns, the
agent does more of it. The next cycle should target that directly rather than extending runs — the
plan's candidate is making `write_file` to a never-read path fail loudly, so editing blind stops
being the path of least resistance.

Any future retry of a termination gate must verify that the *thing under test* was untouched, not
merely that a command exited 0.

---

## Phase E — Durability and safety

Chosen over another tuning cycle because it closes Definition-of-Done items, is almost entirely
deterministic, and needs essentially no model quota while the provider question stays open.
**160 unit tests**, green under `--read-only --tmpfs /tmp:exec --network none`.

### E1 — a killed process loses at most one node, and duplicates nothing (NFR-302)

Proven twice, deliberately: once deterministically, once for real.

**Offline (3 new tests).** A `KeyboardInterrupt` from inside a tool simulates process death —
chosen because it is a `BaseException`, so `execute` does not convert it into an observation the
way an ordinary error would. It propagates straight out of `invoke()`, which is what a killed
process looks like to the graph.

**A detail worth recording, because the first version of the test got it wrong:** on resume the
**crashed node genuinely does re-run**. Its work never committed, so retrying it is correct — that
*is* "at most one node of work lost". The claim under test is narrower: a turn that DID commit must
never run twice. The first test resumed without repairing the failing tool, the retry raised again,
and the `KeyboardInterrupt` escaped and aborted pytest.

**Live SIGKILL.** A real container running `fix-import`, killed with `docker kill` once the
checkpoint showed `turns: 1` and one `write_file` committed. Resumed with the same `thread_id`:

```
resumed at turn 1, 896 tokens spent
turn 2/12   ...
done  |  2 turns
```

Across the whole thread — pre-kill plus post-resume — **0 duplicated tool calls**, and the original
goal survived. It resumed at turn 2 rather than restarting. This is what the gate/execute split has
existed for since Phase B, now measured against a real process death rather than an approval pause.

### E2 — the write boundary is now enforced by the kernel (NFR-201, NFR-204)

The scored suite runs `--read-only --tmpfs /tmp:exec`. Bind mounts are unaffected, so `/workspace`
and the checkpoint database stay writable — which is exactly the boundary. A write outside it now
fails with `OSError: Read-only file system` rather than succeeding quietly.

`count_write_violations()` scans results for that error and the report flags it **above** the table:
NFR-201 is about attempts as much as outcomes, and a write the kernel refused is still the agent
reaching outside its workspace.

The conflict this had to resolve first: `missing-dep` is solved by `pip install`, which writes to
site-packages. Measured rather than assumed — `PIP_USER=1` plus `PYTHONUSERBASE=/tmp/pyuser` sends
that install to the tmpfs while the pre-installed packages stay importable, because the user site is
additive rather than a replacement. A tmpfs mounted *over* site-packages would have masked
pytest/flask/langgraph and broken all five cases instead of fixing one. Verified end to end:
`missing-dep`'s fix still works under `--read-only --network none`.

### Route A for zero egress is FALSIFIED

The Phase D plan claimed the container could reach Ollama on the host while being cut off from the
internet, using Docker network flags alone. **It cannot.** Measured:

| Network | Internet | Host (Ollama) |
|---|---|---|
| `--internal` | blocked (`gaierror`) | **also blocked** (`Network is unreachable`) |

`--internal` severs the host gateway too, so "reach the model, block everything else" is not
available from flags. What *is* proven is the stronger property for everything that does not need a
model: 160 unit tests and `missing-dep`'s entire fix run under `--network none`.

**Restricted egress for a live scored run therefore remains open** and needs a proxy, or a model
served inside the same Docker network. Recorded as an open item rather than quietly satisfied.

### E3 — cost ceilings reported against real numbers (NFR-104, NFR-402)

`tiktoken` was considered and **rejected**: it is OpenAI's tokenisation, and the scored model is
Llama. It would have produced a different model's token count dressed up as precision. The
model-exact tokeniser is a heavy dependency for one bound.

Instead the ceilings use the **provider's own reported counts**, which every row already carries,
plus the character measure the system actually controls. Against the committed baseline:

```
ceilings:
  median tokens/case     3,266 / 60,000   OK   (NFR-402)
  largest result       not recorded in this run   (NFR-104)
  NB: a low median is not efficiency while runs terminate early
```

Rows predating `max_result_chars` say **"not recorded"** rather than `0 / 6,000 OK`. Printing a
zero would claim a check that never ran, which is the precise way a green dashboard lies.

---

## BASELINE 2 — `nemotron-3-super-120b-a12b` — **pass 14/15**

A different configuration, therefore a **new baseline, not a tuning delta**. Nothing about the jump
from 4/15 is attributable to loop design: only the model changed.

| | |
|---|---|
| Date | 2026-08-19 |
| Provider / model | NVIDIA NIM, `nvidia/nemotron-3-super-120b-a12b` |
| Runs | 3 per dev case, 15 total, 0 blocked |
| Traces | `eval/runs/20260818T193917Z/` |

| Case | Baseline 1 (llama-3.1-70b) | Baseline 2 | Turns | Tokens (med) | Tampered |
|---|---|---|---|---|---|
| `fix-import` | 1/3 | **3/3** | 10/12/10 | 34,394 | 0 |
| `add-endpoint` | 0/3 | **2/3** | 11/12/11 | 35,648 | 0 |
| `off-by-one` | 0/3 | **3/3** | 10/9/8 | 28,877 | 0 |
| `broken-fixture` | 0/3 | **3/3** | 8/10/10 | 27,464 | 0 |
| `missing-dep` | 3/3 | **3/3** | 5/4/3 | 6,971 | 0 |
| **Total** | **4/15** | **14/15** | | | |

Verdicts: `done 13, stuck 2`. Ceilings: median 27,852 / 60,000 OK; largest result 2,908 / 6,000 OK -
the first time NFR-104 has been *measured* rather than bounded by assumption.

### Trust checks, same as cycle zero

- **Zero tampering on any pass.** Not one run edited the tests it is judged by (was 5 of 15).
- Zero attempted writes outside the workspace.
- One provider/model across all 15 rows; 0 blocked, so the denominator really is 15.
- `fix-import`, `broken-fixture` and `off-by-one` re-verified to still exit 1 when untouched. The
  rig did not rot, and nothing passed because a reset silently stopped working.

### The diagnosis in cycle zero was wrong, and this is the correction

Every failure bucketed as loop design was the model:

| Symptom, baseline 1 | Bucketed as | Baseline 2 |
|---|---|---|
| 9 of 15 runs never called `read_file` | blind editing | reads throughout; the sole failure made 8 reads |
| all 15 ended `done`, 11 wrongly | termination bug | `done 13, stuck 2` - and both `stuck` runs PASSED |
| 5 of 15 rewrote their own tests | blind editing | zero |

**Cycle 1 was aimed at a real symptom of the wrong cause.** Reverting it was correct, and for a
second reason unknown at the time: it would have been dead weight against a competent model.

Also recorded plainly: the prediction that ">=4/5 will need the Anthropic path, not an open-weight
model" was **wrong**. A free model on the key already in use clears the bar. The error was inferring
a capability ceiling from one model's behaviour instead of probing the cheapest alternative first -
102 models were available on that key the whole time.

### Both `stuck` runs passed, and that is the design working

The harness scores by the check command's exit code, never by the agent's own claim. Two runs hit
the turn cap without declaring success and their fixes were correct anyway. Had the verdict been
allowed to gate the score, those two would have been recorded as failures.

### The token prediction held

Median rose 3,266 -> 27,852 and turns went from 1-9 to 8-12. Cycle zero recorded: *"expect the
median to RISE when premature termination is fixed - a rising token count will be a sign of
progress, not regression."* That is what happened, and it is still well inside the ceiling.

The corollary stands too: baseline 1's cheapness was never efficiency. It was the agent quitting.

---

## Phase G — ten held-out cases (built, verified, not yet scored)

Written because **14/15 is five cases the system was developed against**. Nothing yet shows it
generalises, and the dev set is statistically saturated (1 SD ~ 1.0 run), so no tuning cycle can be
honestly measured until harder cases exist.

### The contamination that cannot be removed, stated rather than hidden

**Every dev trace has been read**, so the choice of cases is informed by knowing where this agent is
strong and weak - precisely the independence a held-out set is supposed to have.

Partial mitigation: all ten are drawn from a **taxonomy of common Python defect classes**, chosen
because they are ordinary bugs and **not** because of anything seen in a trace. None targets a known
weakness, and none targets the directory-read defect fixed in this same phase. A genuinely
independent set would be authored by someone who had never seen the traces. Read the number with
that attached.

### Design, per your decisions

- **Six matched + four harder, to be scored and reported separately.** Merged into one number, a low
  score would be ambiguous between "overfitted to the dev five" and "these are simply harder".
- **Turn cap stays at 12**, same as dev, so the two sets remain directly comparable. Runs already use
  8-12 of 12, so cap-hits are likely and will be reported as a finding rather than hidden.
- **"Harder" means the cause is not where the symptom points** - misdirection, not volume.

### The six matched cases

| id | Domain | Seeded bug | Intended failure (VERIFIED) |
|---|---|---|---|
| `mutable-default` | task queue | `due_tasks(..., collected=[])` shared across calls | `1 failed, 8 passed` - results leak between calls |
| `circular-import` | geometry | `shapes` and `transforms` import each other | `4 passed, 2 errors` - "most likely due to a circular import" |
| `wrong-exception` | settings | store raises `KeyError`, loader catches `ValueError` | `2 failed, 11 passed` - `KeyError` propagates |
| `float-division` | invoicing | `invoice_total(items) // len(items)` | `1 failed, 7 passed` - `12.0 != 12.5` |
| `sort-key` | leaderboard | `key=lambda e: (str(e.score), ...)` | `1 failed, 4 passed` - "9" outranks "10" |
| `missing-return` | path router | `path.rstrip("/")` computed, never assigned | `1 failed, 8 passed` - trailing slash survives |

### The four harder cases

| id | Domain | Seeded bug | Why the obvious fix is wrong |
|---|---|---|---|
| `naive-datetime` | scheduler | `Event.at` does `.replace(tzinfo=None)` | **Decoy verified**: patching the comparison site (where the TypeError points) passes one test and fails another. Only fixing the constructor passes all seven |
| `double-encoding` | subtitles | two `open(..., encoding="ascii")` call sites | **Verified**: fixing one site leaves `1 failed`; both must be fixed |
| `dict-mutation` | inventory | helper `del`s from the dict its caller iterates | the `RuntimeError` names the loop; the `del` is a frame deeper |
| `stale-cache` | pricing | `@lru_cache` on a method whose object holds a mutable rate | the wrong price appears far from the decorator that causes it |

### Verification performed before scoring

- **All ten exit 1** through `scripts/reset.sh` plus the scored check command, each with passing
  tests alongside the failures - so a broken fixture is still distinguishable from a broken
  environment.
- **All ten flip to exit 0 when hand-fixed.** A check that cannot discriminate is invisible until it
  has already corrupted every number taken after it.
- Both "harder" properties were tested directly rather than assumed: the `naive-datetime` decoy
  genuinely misleads, and `double-encoding` genuinely needs both sites.
- `--split dev` still selects exactly 5; `--split heldout` selects exactly 10; no duplicate ids.

### Also in this phase: the directory-read bug fix

`read_file` on a directory returned a bare `IsADirectoryError`. The agent retried the same path with
a trailing slash, got the identical message, and burned 3 of its 12 turns - and that case passes in
11. It now raises a message naming `run_shell` and `ls`.

**No delta is claimed.** On a saturated dev set none can be measured; the dev re-run is a regression
guard only.

---

## Phase G — HELD-OUT SCORED: 29/30

Ten cases the system was never developed against, scored once, 3 runs each, 0 blocked.
Traces: `eval/runs/20260819T041221Z/`.

| Group | Score | The question it answers |
|---|---|---|
| **Matched six** | **17/18** | Was 14/15 fitted to the dev five? |
| **Harder four** | **12/12** | How much headroom is left? |
| **Total** | **29/30** | |

Dev was 14/15 (93.3%); matched held-out is 17/18 (94.4%). **Statistically indistinguishable.**

### The reading, decided before the data existed

The plan pre-registered three outcome shapes. This is the "near dev" branch: **the 14/15 was real,
not overfitted.** These domains and defect classes were never seen during development, and the
result held.

### The prediction that was wrong: "harder" was not harder

The four cases built to be harder scored **12/12** - better than the matched six. The one failure in
the entire run was `sort-key`, a *matched* case.

The difficulty axis was chosen deliberately: **the cause is not where the symptom points.** Both
mechanisms were verified to work before scoring - patching `naive-datetime` at the comparison site
genuinely leaves a test failing, and `double-encoding` genuinely needs both call sites fixed. The
agent handled both anyway, 3/3 each.

So misdirection is not what challenges this model. Whatever its ceiling is, it is not "the traceback
points somewhere other than the fix". That is a real finding about what a harder case would have to
look like, and it means **this held-out set has almost no headroom either** - a future Phase F still
lacks a set with room to measure against.

### The one failure, and the cap

`sort-key` run 0: `stuck` at 12 turns, 12 calls, no tampering. The other two runs passed in 7 and 8
turns, so this was variance rather than an impossible case.

**2 of 30 runs hit the turn cap, and one of them PASSED** (`naive-datetime` run 1). Scoring is by the
check command's exit code, never the agent's own claim - had the verdict gated the score, that run
would have been recorded as a failure. Keeping the cap at 12 rather than quietly raising it is what
made this visible.

### Trust checks

- **Zero tampering on any pass**, zero attempted writes outside the workspace.
- One provider/model across all 30 rows; 0 blocked, so the denominator really is 30.
- `sort-key`, `naive-datetime` and `stale-cache` re-verified to still fail untouched after the run.
- Ceilings: median 15,473 / 60,000 tokens; largest result 4,357 / 6,000 chars. Both inside.

### The caveat that does not go away

**Every dev trace had been read before these cases were written.** The mitigation - deriving all ten
from a taxonomy of ordinary Python defect classes rather than from observed weaknesses - is partial.
A genuinely independent set would be authored by someone who had never seen the traces. 29/30 should
be read with that attached.

---

## Phase H — restricted egress (NFR-205) — **the last DoD item, now met**

Scored runs reach the model through an allowlisting proxy and have **no other route off the
machine**. The agent container sits on an `--internal` Docker network whose only neighbour is the
proxy; the proxy alone is attached to a network with an outward route.

### Why no TLS interception was needed

HTTPS through a forward proxy uses **CONNECT, and the destination hostname travels in that line in
cleartext**. The proxy allowlists on the hostname without decrypting anything, so the model
connection stays end-to-end encrypted and no CA has to be injected into the image. That is what kept
this a configuration job rather than a security project.

`httpx` runs with `trust_env=True`, so the `openai` SDK picks up `HTTPS_PROXY` on its own:
**`agent/provider.py` needed no change at all.**

### Verified with the real agent image, not asserted

| Check | Result |
|---|---|
| Model host through the proxy | `http 200` |
| Any other host through the proxy | `ProxyError 403 Filtered` (proxy logs "refused on filtered domain") |
| **Raw IP, no proxy configured** | `OSError [Errno 101] Network is unreachable` |

**The third row is the one that decides it.** The first attempt at that test returned "couldn't
resolve host" - which is DNS failing, not routing blocked, and would be bypassed by dialling an IP
directly. Re-testing by raw IP showed a genuine routing failure. A DNS-only barrier looks identical
in a careless test and is worth nothing.

### Regression: dev suite under the restriction

**15/15**, against 14/15 unrestricted. **No improvement is claimed** - one run is inside noise at
n=15, and this run's purpose was to show nothing broke. It did not. `missing-dep` still installs its
package, because `/etc/pip.conf` resolves from `/wheels` and never touches the network.

Every row now records `egress: restricted`, because whether a run was restricted is part of what the
number describes, exactly like the model is.

### Two bugs found while wiring it in

- **CRLF.** Python's `write_text` translated newlines on Windows, so the Linux container read
  `User tinyproxy\r` and died with "Syntax error on line 1". Pinned with `newline=""`. Same family
  as the `.gitattributes` lesson from Phase A: a text file crossing an OS boundary needs its line
  endings fixed explicitly.
- **`ensure_egress()` reported success for a container that had already died.** `docker run -d`
  exiting 0 only means the container was CREATED. It now polls `State.Running` and prints the
  container log on failure. Without that, a scored run would have started with no proxy at all -
  precisely what the preflight exists to prevent.

### Design notes

- The allowlist is **derived from the configured base URL**, not hardcoded, so pointing the agent at
  a different provider moves the permitted host with it. A hand-maintained list would drift and
  silently over-permit or break runs.
- Each host is an **anchored regex** (`^integrate\.api\.nvidia\.com$`). A bare substring would let
  `evil-nvidia.com` through.
- `NO_PROXY` is set **empty** on purpose: any exemption would be a hole, and the container has no
  other route regardless.
- A `--split` run **refuses to start** without the proxy, naming the override. A scored number
  produced with egress silently open would be the quiet untruth this rig exists to prevent.
- The **interactive CLI is deliberately unrestricted**. The requirement is about the scored suite,
  and forcing the proxy on interactive sessions adds friction with no measurement benefit.

---

## Phase I2 — calibration: one axis discriminates, and the reason is not the one predicted

Three candidate difficulty axes, one pilot each, 3 runs each, cap raised to 25 so the budget would
not be the limiting factor. Traces: `eval/runs/20260819T063107Z/`.

| Axis | Score | Turns | Tokens | Predicted | Outcome |
|---|---|---|---|---|---|
| `pilot-multibug` — 4 independent bugs | **1/3** | 11/19/15 | 61,907 | discriminates | **correct** |
| `pilot-crosscut` — 6 coordinated call-site edits | 3/3 | 10/10/11 | 34,051 | probably discriminates | **WRONG** |
| `pilot-large` — ordinary bug among 36 modules | 3/3 | 5/8/7 | 15,325 | does NOT discriminate | **correct** |

Predictions were recorded before the run. Two of three held.

### The model was wrong, and the corrected one is sharper

The working hypothesis was **required edits**. `pilot-crosscut` refutes it: six coordinated edits
across three modules, and it passed 3/3 in 10-11 turns.

What actually separates the axes is **the number of independent DIAGNOSES held at once**:

| Case | Diagnoses | Edits | Result |
|---|---|---|---|
| crosscut | **1** | 6 | 3/3 |
| large | **1** | 1 | 3/3 |
| multibug | **4** | 4 | **1/3** |

Six mechanical applications of one insight are easy. Four separate insights held simultaneously are
not.

### How it fails is the interesting part — it thrashes, it does not run out of budget

Both failures ended `stuck` at **11 and 15 turns against a cap of 25**, having made **zero writes**.
The final check output was identical to the untouched state: no progress whatsoever.

```
run 0:  ls, pytest, read averages, read counting, read normalise,
        read averages, read averages, read search,
        read counting, read counting, read counting   <- thrash detector fires
```

The `reflect` repeat-detector (three identical call signatures) fired correctly - the agent WAS
looping - and terminated the run less than halfway through its budget. Given four bugs to track, it
re-read the same files instead of editing any of them.

**This is the first genuine capability limit this project has found**, and it is not a budget limit.

> **RETRACTED by the Phase I4 scoring run — do not resume from this claim.** The same case, at the
> same cap, on the same model, with no agent change in between, scored **3/3** when the full set was
> run (15/17/15 turns, `done` every time). A 1-of-3 that reproduces at 3-of-3 is variance, not a
> capability limit. The failure below happened and the trace is real; the *conclusion* drawn from it
> was wrong. See "Phase I4" for what replaced it.

### It also produces the first evidence-backed tuning hypothesis

The thrash detector requires three *identical* signatures and then ends the run. On these traces it
fired at turn 11 of 25. Whether the agent would have broken out on turn 12 is unknown, **because the
detector stopped it**. That is a specific, testable question of exactly the kind Phase F has lacked:

> Does the repeat-detector end runs that would have recovered?

Note this cuts both ways and must be measured, not assumed: the detector exists because unbounded
repetition burns budget for nothing, and loosening it may simply buy more thrashing.

> **RETRACTED with the claim above.** This hypothesis rested on two `stuck` runs of one case; that
> case then passed 3/3 unchanged. Its whole evidence base is a single unreproduced failure, which is
> not enough to spend a tuning cycle on. The question remains askable — it is simply no longer
> *evidenced*, and the distinction is the point of this file.

### Verification

9 scored, 0 blocked after retry (one run was rate-limited and superseded by its retry - last row
wins), zero tampering, zero attempted writes outside the workspace, all runs egress-restricted.

---

## Phase I3 — the multibug set, built on the one axis that calibrated

Nine new cases on the axis the pilots identified, joining `pilot-multibug` for a ten-case set. The
pilot **stays in** rather than being dropped: it is already scored, and removing a case because you
have seen its result is how a set gets quietly tuned.

### Construction

Every defect is **independent and one line to fix**, so a case's difficulty is purely the number of
separate diagnoses it demands — the quantity the calibration showed actually discriminates. Twelve
defect types were drawn from ordinary Python mistakes and combined without repetition within a case:

`split_spaces` · `wrong_denominator` · `early_return` · `missing_lower` · `off_by_one` ·
`int_division` · `str_sort` · `missing_return` · `wrong_comparison` · `mutable_default` ·
`inclusive_slice` · `missing_abs`

| Bugs | Cases |
|---|---|
| 3 | `multi-orders`, `multi-metrics`, `multi-registry` |
| 4 | `multi-billing`, `multi-routing`, `multi-analytics`, `pilot-multibug` |
| 5 | `multi-warehouse`, `multi-payroll`, `multi-catalogue` |

The spread is deliberate. A set built entirely at one bug count would say only *whether* the limit
exists; a spread says **where it sits**, which is the more useful measurement and costs nothing extra
to collect.

Each case keeps the Phase A shape that has been paid for once already: its own `pyproject.toml` with
`pythonpath`, an `__init__.py` with no re-exports, a README that never hints at the bug, and one
correct module (`shared.py`) whose two tests pass — so a healthy environment stays visibly different
from a broken one even while several modules are failing.

### Verified in both directions, which is stronger than the usual check

Every case was confirmed to fail untouched **and** to flip to green when fully fixed. It was also
confirmed to **still fail with exactly one bug remaining** — the check that matters for this axis
specifically, because partial completion being indistinguishable from success would destroy the
entire measurement:

```
multi-orders     3 failed, 2 passed    all-fixed: exit 0    one-left: exit 1
multi-metrics    3 failed, 2 passed    all-fixed: exit 0    one-left: exit 1
multi-registry   3 failed, 2 passed    all-fixed: exit 0    one-left: exit 1
multi-billing    4 failed, 2 passed    all-fixed: exit 0    one-left: exit 1
multi-routing    4 failed, 2 passed    all-fixed: exit 0    one-left: exit 1
multi-analytics  4 failed, 2 passed    all-fixed: exit 0    one-left: exit 1
multi-warehouse  5 failed, 2 passed    all-fixed: exit 0    one-left: exit 1
multi-payroll    5 failed, 2 passed    all-fixed: exit 0    one-left: exit 1
multi-catalogue  5 failed, 2 passed    all-fixed: exit 0    one-left: exit 1
```

Split filters re-confirmed after registration: `dev` selects 5, `heldout` selects 10, `multibug`
selects 10, `pilot` selects the 2 rejected axes (kept for the record, not scored again).

Cap is **25 turns**, matching the pilots. At the dev cap of 12 this set would measure the budget
rather than the agent — the pilot failures thrashed to a halt at turns 11 and 15 without ever
approaching 25.

### Rig defect found mid-run: the egress proxy could not resolve, and said it was healthy

The first attempt at the I4 scoring run blocked on every attempt of its first two case-runs.
Recorded reason: `APIConnectionError: Connection error.` Not a rate limit, and not the agent.

Chain, in the order it was actually established:

| Step | Finding |
|---|---|
| Harness output | `BLOCKED`, retried at 80s and 160s, blocked again |
| Trace row | `APIConnectionError` — a connection failure, not a 429 |
| Proxy container | **Up 2 hours, State.Running true** — the preflight's only check |
| Proxy log | `opensock: Could not retrieve address info ...: Try again` (EAI_AGAIN) |
| Fresh container, same lookup | **http 200** — host, network and free tier all fine |
| `getent hosts` in the proxy | resolves, 75.2.113.119 |
| `getent ahosts` in the proxy | **empty** |

That last pair is the whole diagnosis. `getent hosts` is an A-only lookup; `getent ahosts` is the
dual-family AF_UNSPEC query that tinyproxy actually makes. Docker's embedded resolver at 127.0.0.11
was answering the first and returning nothing for the second, so every CONNECT died while the
obvious health check looked green.

**Fix:** the proxy is created with `--dns 8.8.8.8 --dns 1.1.1.1`. This does not widen egress —
which hosts the proxy may reach is decided by the CONNECT filter, not by which resolver it asks.
Verified after the change: allowed host `200`, non-allowlisted host `403`, and the proxy rebuilt by
`ensure_egress()` itself rather than by hand.

**The preflight was the real defect.** Phase H already learned that `docker run -d` returning 0 only
means *created*, and added a Running poll. This is the same lesson one level up: **Running is not
usable.** `_proxy_can_resolve()` now probes `getent ahosts` for every allowlisted host and refuses
the run with the command that fixes it. Probing `getent hosts` instead would have passed the broken
proxy straight through, which is why the check names the AF_UNSPEC form explicitly.

**A wrong turn worth recording:** the first hand-built replacement proxy returned 403 on *every*
host, which looked like a filter bug. It was not — the manual `docker run` omitted
`MSYS_NO_PATHCONV=1`, Git Bash rewrote the two config mount paths, and tinyproxy silently fell back
to its built-in default, which permits only localhost. The rig was fine; the test of the rig was
broken. Same family as the CRLF fault in Phase H: on this machine, a path or a line ending crossing
into a Linux container is guilty until proven innocent.

No score is affected. Blocked runs carry no result by design, which is exactly why this surfaced as
`0 scored` rather than as a bad number.

---

## Phase I4 — the multibug set, scored: the axis does not discriminate either

`eval/runs/20260819T070445Z/`, one model, egress restricted, 3 runs per case, cap 25.

```
pass 25/26   (4 blocked, excluded - not counted as failures)
verdicts: done 25, stuck 1
```

| case | bugs | pass | turns | tokens (med) |
|---|---|---|---|---|
| `multi-metrics` | 3 | 3/3 | 11/12/13 | 28,701 |
| `multi-orders` | 3 | 3/3 | 11/12/13 | 27,729 |
| `multi-registry` | 3 | 3/3 | 12/15/16 | 38,415 |
| `multi-analytics` | 4 | 3/3 | 16/12/13 | 34,355 |
| `multi-billing` | 4 | 2/3 | 13/12/14 | 30,833 |
| `multi-routing` | 4 | 3/3 | 16/16/16 | 43,471 |
| `pilot-multibug` | 4 | 3/3 | 17/15/15 | 56,983 |
| `multi-payroll` | 5 | 2/2 | 17/17 | 51,727 |
| `multi-warehouse` | 5 | 3/3 | 18/17/23 | 55,273 |
| `multi-catalogue` | 5 | **no runs** | — | — |

### The verdict, against the rule fixed in advance

Phase I2 set the band before any data existed: 40-70% discriminates, **above 85% means reject the
axis**. This set scores **96%**. The rule applies to its author too.

**Independent-bug count is not a difficulty axis for this agent.** That is now three axes tried and
three rejected - misdirection (Phase G, 12/12), cross-cutting edits (`pilot-crosscut`, 3/3), and
independent diagnoses (this set, 25/26). The Phase I exit criterion explicitly allows this outcome:
*"an explicit finding that none of the three axes discriminates this agent, which ends the search
rather than extending it by guesswork."* That is the result. **The search ends here.**

### Two retractions, both mine, both from this phase

1. **"The first genuine capability limit this project has found"** - withdrawn. `pilot-multibug`
   scored 1/3 in calibration and **3/3** here, unchanged, at the same cap on the same model. A
   1-of-3 that reproduces at 3-of-3 is variance.
2. **The thrash-detector tuning hypothesis** - withdrawn with it. Its entire evidence base was those
   two `stuck` runs.

The project already had a standing lesson for this shape - *probe before theorising when a number
looks structurally wrong* - and it was learned from a model swap. This is the same error in the
other direction: **I built a theory on n=3 and it did not survive n=30.** The lesson generalises to
"a single surprising result is a hypothesis, never a finding", and belongs in `CLAUDE.md` as such.

### What DID survive, and it is the useful part

Bug count buys **turns**, monotonically and predictably:

| bugs | runs | turns (range) | mean |
|---|---|---|---|
| 3 | 9 | 11-16 | **12.8** |
| 4 | 12 | 12-17 | **14.6** |
| 5 | 5 | 17-23 | **18.4** |

**About +2.8 turns per additional bug**, and the first quantified predictive relationship this
project has. It says the honest way to build a discriminating case is to keep going up this axis
until the turn budget binds: extrapolating, ~8 bugs lands near 27 turns and would exceed a cap of
25. It also says a 12-turn cap - the dev and held-out default - would have failed most of this set
on budget alone, measuring the cap rather than the agent.

Nothing here is at the cap: **0 of 26 runs hit 25 turns**, max observed 23.

### The set is incomplete, and that is stated rather than smoothed over

`multi-catalogue` has **zero** completed runs and `multi-payroll` has two of three. The set is
therefore measured on **9 of 10 cases**, and the absent case is a 5-bug one - the hard end. The
conclusion does not turn on it (rejecting the axis needs a score far above the band, and 25/26 is
far above it whichever way one case falls), but the number is not a clean 30 and must not be quoted
as one. `--continue` completes it when quota returns.

### Why it is incomplete: the free tier, measured rather than assumed

Six identical first-calls, 10s apart, after the 26 runs had completed:

```
attempt 0: OK              2.4s
attempt 1: RateLimitError  0.3s
attempt 2: RateLimitError  0.5s
attempt 3: RateLimitError  0.4s
attempt 4: RateLimitError  0.2s
attempt 5: OK              1.2s
```

The tier rejects roughly two of every three requests, instantly and at random - not by size (a
16,000-token request with tools succeeded), not by gap (`--pace 5` and `--pace 60` behaved
identically), and not by exhausted credits (calls still succeed). A turn survives 3 SDK attempts
about 70% of the time, so a 17-turn run completes with probability near **0.7^17, under 1%**.
Retrying cannot fix that, which is why the resume attempts were stopped rather than left looping.

**Correction, made after this was first written.** An earlier draft said blocked runs "failed on the
first model call, before any work", citing `turns=0, tokens=0, messages=0` in the row. That is an
**artifact, not evidence**: `harness.py` records a `ProviderUnavailable` run with `state=None` by
design, so those fields read zero no matter how far the run actually got. The only real datum is the
26.7s elapsed, which is consistent both with three rate-limited retries on the first call and with
several completed turns before a later one. **How far these runs got is unknown.**

What makes them safely excludable is unchanged and does not depend on that claim: a blocked run
never reaches its check command, so **no score is ever produced for it**. There is nothing to
exclude from the denominator except an absence.

**A suspected case-specific anomaly, raised and then disproved within the hour.** `multi-payroll`
run 2 blocked on 12 consecutive attempts across four resume passes, which at the measured failure
rate looked far too improbable to be chance, so it was written up as likely case-specific. A control
settled it: `multi-orders` - 3 bugs, passed 3/3 an hour earlier - blocks identically right now. The
effect is **tier-wide**. The apparent anomaly was a selection artifact: `multi-payroll` run 2 is the
first case-run every resume pass attempts, so it drew the worst slot every time.

**The probe-based estimate was also wrong.** A 6/8 success rate on isolated calls predicted ~77%
completion odds for a 17-turn run; six consecutive real runs then blocked. Isolated probes do not
predict sustained load - plausibly the throttle responds to the request *pattern* an agent produces
rather than to individual calls. Treat single-request probes as a liveness check only, never as a
capacity estimate.

**Cost, now known:** this run consumed **1,105,411 tokens** for 26 scored runs (~42.5k each) and
saturated the free tier. A 30-run scoring pass is a once-per-day operation on this key, which is a
hard constraint on how often any future tuning cycle can be measured - and worth more than the pass
rate it produced.

### Trust checks

26 scored, 4 blocked and excluded, **0 tampering**, **0 attempted writes outside the workspace**,
one model (`nvidia/nemotron-3-super-120b-a12b`) across every row, all fixtures verified in both
directions before scoring, egress restricted throughout. Median 42,950 tokens against the 60,000
ceiling; largest single tool result 3,811 chars against 6,000.

---

## Phase J1 — cost probe on the first real repository

One case, `real-humanize`, vendored from python-humanize/humanize at the parent of the fix commit
for *"Carry `metric()` to the next SI prefix when rounding reaches 1000"* (#328). Source at the
parent, tests from the fix. 73 files, suite 3.8s, verified both directions offline:
**4 failed / 689 passed** untouched, **693 passed** with the upstream diff applied.

The probe exists so six cases are not sized against a guess. It cost two runs and found two defects.

| | probe 1 | probe 2 (after fixes) |
|---|---|---|
| tokens | 260,531 | **136,077** |
| turns | 18 (cap 30) | 12 |
| largest tool result | **11,340 chars** (cap 6,000) | 4,784 |
| verdict | `compact` — out of budget | `done` |

### Defect 1 — `shrink()` bounded lines, not characters (NFR-104)

`shrink()` takes 30 head + 20 tail lines when a result has enough lines, and never checked the
character total in that branch. Real pytest output has long lines, so a 62KB result came back at
**11,340 chars against a 6,000-char cap**. The practice fixtures never had lines long enough to
expose it, which is why 161 tests passed over it. Fixed by clamping both halves to `cap // 2`, with
a test built from long lines rather than a long single line.

### Defect 2 — the fixture created a rabbit hole unrelated to the bug

Probe 1 spent **nine of eighteen turns** trying to `pip install pytest-codspeed`, `pip install -e
.[tests]`, `hatch-vcs`, and even `--index-url https://pypi.org/simple` against a sandbox with no
egress. Cause: `tests/test_benchmarks.py` needs a plugin the offline image lacks, so the agent's own
`pytest -q` errored — while the check command hid it behind `--ignore`.

**The agent and the scorer were running different suites.** That is the Phase A "fails for the wrong
reason" trap one level up, and hiding it with `--ignore` would have been the worse fix: the agent
would still have seen a broken suite while a different one was scored. The benchmark file is dropped
from the fixture instead, so `pytest -q` **is** the scored command, byte for byte.

### What the agent actually does on a real repository

Probe 2's trace is the useful part. It diagnosed the bug **correctly** — all four failures, and the
root cause stated plainly:

> *"the function is not scaling correctly when the value is just below a threshold ... Instead of
> scaling up to the next prefix (k, M, m), it stays in the current prefix"*

Then it wrote `/workspace/debug.py`, never edited `src/humanize/number.py`, and spent its output
budget on a long analysis that hit `max_tokens` mid-sentence. A text-only reply after at least one
tool call means `done`, so the loop terminated on a correct diagnosis and an unapplied fix.

**Understanding the bug and editing the file are separate capabilities, and only the first is
present.** This is the first failure this project has that is neither saturation nor a rig fault.

### Sizing

At ~136k tokens/run the free tier supports **~8 runs/day**, so 6 cases x 3 runs is ~2.5 days —
within the plan, no re-sizing needed. The turn cap of 30 is **not** binding (12 used); the limits
that bite are the token budget and the model's own premature termination.

---

## Phase J2/J3 — six real-repository cases, verified before any scored run

Split `real`. Source vendored at the **parent** of a genuine upstream bug-fix commit; tests taken
from the fix. No nested `.git`, so `scripts/reset.sh` works unchanged and Phase J adds no rig code.
The fix diff is never committed - `spawn()` mounts the whole project at `/app` and the agent has
`run_shell`, so verification fetches the fix SHA from upstream on the host instead.

| case | files | suite | untouched | hand-fixed | the actual bug |
|---|---|---|---|---|---|
| `real-humanize` | 73 | 3.8s | 4 failed, 689 passed | 693 passed | `metric()` does not carry to the next SI prefix when rounding reaches 1000 |
| `real-cachetools` | 41 | 5.2s | 1 failed, 290 passed | 291 passed | `TLRUCache` silently keeps a stale value when an expired entry is overwritten |
| `real-more-itertools` | 39 | 58.9s | 2 failed, 730 passed | 732 passed | `running_min`/`running_max` are not stable |
| `real-click` | 156 | 9.1s | 6 failed, 1857 passed | 1863 passed | progress bar does not land on its final position |
| `real-rich` | 548 | 10.3s | 1 failed, 926 passed | 927 passed | `print` with `end=` mishandles empty input |
| `real-markdown` | 443 | 8.5s | 3 failed, 779 passed | 782 passed | mixed `=`/`-` characters accepted in Setext-style headings |

Sizes span 39 to 548 files and fixes span one line to 43, which is the spread the mixed-scale
decision asked for. `files` and `suite_seconds` are recorded per case so a low score can be
attributed to **scale** rather than **difficulty** - the mitigation for choosing mixed scale.

### The dominant hazard with real repositories, found the hard way

**Three of six repos were not green at their own fix commit.** rich had 7 failures, click had 25,
markdown had a collection error, and humanize needed a pytest plugin the offline image lacks. Every
cause was environmental - no pager binary, pygments version, terminal width, missing optional deps -
and none related to any bug.

Each would have produced a case whose check **can never exit 0**: a guaranteed failure indistinguishable
from the agent being bad. With authored fixtures this trap was rare; with real repositories it is the
**common case**, and only the both-directions check catches it.

The fix is always to remove the offending test file from the fixture, never to hide it behind
`--ignore` in the check command. **The agent must see exactly the suite that is scored** - Phase J1
measured what happens otherwise: nine of eighteen turns spent installing a plugin, because the agent's
`pytest -q` was broken while a different, quieter suite was being graded. Dropped files:
2 from rich (553), 2 from click (158), 1 each from markdown and humanize.

Repo runtime dependencies are pre-installed in the image (`freezegun`, `attrs`, `pygments`,
`markdown-it-py`) ahead of the `pip.conf` line, because the sandbox has no egress and setup turns are
free-tier quota spent on nothing.

---

## Phase J5/J6 — the real-repository baseline: pass 0/18

`eval/runs/20260820T052036Z/`, 6 cases x 3 runs, cap 30, budget 400,000, one model
(`nvidia/nemotron-3-super-120b-a12b`), egress restricted throughout.

```
pass 0/18   (0 blocked)
verdicts: compact 10, done 5, stuck 3
turns  min/med/max: 1 / 26 / 30  (cap 30, 3 at cap)
tokens min/med/max: 6,837 / 245,713 / 255,824   total 3,543,685
```

| case | files | pass | turns | tokens (med) | verdicts |
|---|---|---|---|---|---|
| `real-more-itertools` | 39 | 0/3 | 30/30/30 | 198,584 | stuck x3 |
| `real-cachetools` | 41 | 0/3 | 28/20/25 | 245,713 | compact x2 done x1 |
| `real-humanize` | 73 | 0/3 | 10/20/1 | 86,422 | done x2 compact x1 |
| `real-click` | 156 | 0/3 | 28/27/26 | 255,043 | compact x3 |
| `real-markdown` | 443 | 0/3 | 8/22/12 | 113,388 | done x2 compact x1 |
| `real-rich` | 548 | 0/3 | 29/26/25 | 249,883 | compact x3 |

### Read against the band fixed in advance

0% is **below the 20% floor**, which the rule calls "too hard to measure against - do not celebrate
a hard set". But the same rule names the exception that applies here: **"or a higher cap if cap-hits
dominate."**

They dominate. **13 of 18 runs (72%) ended on a resource limit rather than a decision:**

- **10 `compact`** - every one at >=235k tokens, i.e. precisely the 240k compaction threshold
- **3 `stuck`** - all three at the 30-turn cap, 30/30/30
- **5 `done`** - the only runs the agent itself chose to end

So 0/18 is **not** evidence that the agent cannot fix real bugs. On `humanize` it stated the root
cause correctly - *"instead of scaling up to the next prefix, it stays in the current prefix"* - and
then ran out of room before applying it. The prescribed response is to raise the budget, **not** to
dilute the repositories.

### The compaction layer is now earned, by the trigger v1 specified

v1 deferred compaction with an explicit condition: *"compact verdicts dominate the baseline
distribution."* Across **60 fixture runs it never appeared once**. Here it is **10 of 18**.

That is the first deferred layer in this project's history to be justified by evidence rather than
by prediction, and it is the single most valuable output of Phase J - more than the pass rate.

### Rig defect found: the scored check had no timeout

One run hung for **25 minutes** on `cd /workspace && pytest`, and the suite could not proceed until
the process was killed by hand. Cause: `harness.py` ran `case["check"]` through `subprocess.run`
with **no timeout at all**. An agent edit that makes a parser loop then hangs the entire scored run
with no diagnosis. Fixed: `CHECK_TIMEOUT = 600` (10x the slowest real suite), and a timeout scores
as a **FAIL** - the suite genuinely did not pass, and the agent's own edit is why.

**A misdiagnosis worth recording.** This was first blamed on `run_shell`'s timeout, on the theory
that killing `/bin/sh` orphans a grandchild holding the stdout pipe. A test written to prove it
**passed** - `run_shell` bounds a forked compound command correctly. The hung process belonged to
the harness, not the tool; the agent's own calls are bare `pytest -q`, because `run_shell` sets
`cwd` rather than prefixing `cd`. Seeing a `pytest` in the process list and not checking which
caller owned it is the whole error. The test is kept as a regression guard with a corrected
docstring.

Practice fixtures could not have surfaced any of this: their suites are fast and always terminate.

### Contamination, stated

`real-markdown` run 1 was interfered with - the hung process was killed by hand mid-run, and the
`compact` verdict it recorded is partly a consequence of that intervention. The case is 0/3 either
way and the headline does not move, but the run is not a clean observation and is flagged rather
than quietly counted.

### Cost

**3.54M tokens for 18 runs** (~197k each), against an estimate of 136k. `compact` runs are what
raise the mean: they burn the full budget before stopping. The median case is **245,713 tokens
against the 60,000 ceiling** - real repositories simply cost about 4x what v1 was scoped for, and
that ceiling needs restating rather than quietly failing.

Two free-tier keys were used: the first was exhausted mid-run and swapped. Model and endpoint were
identical, so this remains one coherent measurement - a key governs quota, not behaviour.

### The next question, and it is one cheap run

Re-run a single case with the budget raised from 400k to 1M. If it then passes, the ceiling is
budget and compaction is the highest-value work in v2. If it still fails, the limit is capability
and the set needs diluting after all. **One run decides which of the two paths Phase K takes.**

---

## Item 12 — the budget experiment: the starvation hypothesis is REFUTED

Phase J's 0/18 had 72% of runs ending on a resource limit, which suggested the agent was starved
rather than incapable. `real-click` was the probe: it ended `compact` 3/3 and twice reached **one
remaining failure out of six**, apparently cut off mid-solve.

Three runs with resources raised, one variable at a time:

| resources | verdict | turns | tokens | failures | writes |
|---|---|---|---|---|---|
| 400k / 30 (baseline) | compact x3 | 28/27/26 | 255k | **6->1, 6->1**, 6->6 | 1, 1, 0 |
| 1M / 30 | stuck | 30 (at cap) | 281k | 6->6 | **0** |
| 1M / 60 | stuck | **35 of 60** | 516k | 6->6 | **0** |
| 1M / 60 | done | **27 of 60** | 302k | 6->6 | **0** |

**More room produced less progress.** Not one of the three used its budget or its turns: one hit the
old cap, one was stopped by the thrash detector at turn 35 of 60, and one terminated *itself* at turn
27 with two-thirds of everything unspent.

Raising the budget alone was not even a valid test - it simply moved the wall from the token
threshold to the 30-turn cap at 281k tokens. `AGENT_MAX_TURNS` was added alongside `AGENT_BUDGET` so
the hypothesis could be tested properly, as overrides rather than edits to `tasks.jsonl`.

### The real finding: the agent reads, and does not write

Across **all 21 real-repository runs**:

```
write_file calls:   7
read_file calls:  260
runs with ZERO writes: 13 of 21
runs with MORE than one write: 0
```

A 37:1 read-to-write ratio. The failing run at 1M/60 read `src/click/_termui_impl.py` **eleven times
consecutively**, then `cat`-ed the same file three more times until the repeat detector fired.

And the converse holds: **every run that made progress made exactly one write.** `real-click`'s fix
is 43 lines in a single file, so one `write_file` is all it takes - the two runs that fixed five of
six failures each did exactly that.

**The limit is not diagnosis, and not budget. It is committing to an edit.** On `humanize` the agent
stated the root cause correctly and never wrote. Given a million tokens it re-read one file eleven
times instead of changing it.

### What this rules in and out

- **Item 13 (compaction) is NOT justified by this experiment.** The `compact` verdicts were real, but
  removing the ceiling does not convert failures into passes - runs given the room do not use it. The
  10-of-18 `compact` distribution earned a hypothesis; this experiment tested it and it did not hold.
- **Item 13-alt (dilute) does not address the finding either.** A smaller repository does not make an
  agent commit an edit; the two successful runs happened on the *unmodified* 156-file case.
- The pre-registered table routed "ends `done`/`stuck` with room to spare" to 13-alt. That routing
  was written before the write-ratio was visible, and the honest reading is that **neither branch
  targets the measured cause**. Recorded rather than force-fitted.

### Cost and caution

Three runs, ~1.1M tokens. Variance remains large - the same case, unchanged, has produced both
`6->1` and `6->6`. Three runs is not many, and this project has already been burned twice by
treating n=3 as settled. **This refutes starvation; it does not by itself establish the write-
reluctance figure as stable.** The 7:260 ratio spans 21 runs, which is firmer ground than the
verdicts.

---

## Cycle 2 — "an empty reply is not a completion" — REVERTED

The first tuning cycle this project has been able to run since cycle 1, because the real-repository
set is the first one with headroom. Followed the eval-cycle discipline: read traces, bucket, fix the
largest bucket, 3 runs per case, keep or revert.

### Bucketing (21 real-repository runs)

```
 6  termination: `done` while tests still failing   <- largest, fixed this
 5  budget spent, had written
 5  budget spent, never wrote
 4  no strategy (cap, no repeats)
 1  thrashing
```

**The bucket table sent me somewhere I did not expect.** The intended change was a prompt edit
targeting write-reluctance (7 writes against 260 reads). Bucketing put termination first, and the
skill's own table maps `done` with the test still failing to **reflect logic, not the prompt**. The
prompt change was dropped unmeasured.

### Hypothesis

Five of those six runs ended with `stop_reason: "length"` - the model cut off by `max_tokens`
mid-sentence - and four left a message with **zero content blocks**. `reflect` was reading silence as
"finished". So: an assistant reply carrying no text is not a claim of success; return `continue`.

Deliberately **not** gated on the tests passing. Cycle 1 tried that and was reverted: rewarding a
green suite taught the agent to rewrite the tests instead of the code, and tampering went 5/15 to
5/5. This rule says nothing about tests and cannot be gamed the same way.

### Measurement — 3 cases x 3 runs, identical settings (400k / 30)

| case | before | after | tokens (med) |
|---|---|---|---|
| `real-humanize` | 0/3 done x2 compact x1 | 0/3 **done x3** | 86,422 -> 107,194 |
| `real-markdown` | 0/3 done x2 compact x1 | 0/3 **stuck x2** compact x1 | 113,388 -> 195,758 |
| `real-cachetools` | 0/3 compact x2 done x1 | 0/3 **compact x3** | 245,713 -> 252,523 |
| **total** | **0/9** | **0/9** | **+24% to +73% cost** |

### REVERTED

Zero delta on the pass rate, and cost rose by up to 73%. The Iron Law: *a change with a zero delta is
reverted, including one that seems right.* `agent/graph.py` and `tests/test_reflect.py` are back at
HEAD; 166 tests green.

**The mechanism worked - the outcome did not.** `real-markdown` moved from `done x2` to `stuck x2`,
so the guard fired and did convert premature completions into continued work. The agent then spent
the extra turns without fixing anything. Stopping a run from quitting early does not make it
succeed; it just makes it stop later and cost more.

### What the cycle taught, which is worth more than the change

**The defect has two shapes, and the fix only covered one.** Both end `stop_reason: "length"` and
both are misread as `done`:

- **empty message, zero content blocks** - what the baseline sampled, and what the guard catches
- **a ~49,000-character reply truncated mid-sentence** - what all three new `humanize` runs produced,
  which the guard cannot see, because that text is not empty

The general fix keys on `stop_reason`, not on emptiness. `reflect(state)` cannot see it today - it
takes only state, and §13 fixes the state shape at nine fields - so that is a larger change and
belongs in its own cycle rather than bolted onto this one.

### A reporting artifact, not a scoring error

One case printed `pass 0/2` while `summary.jsonl` held three scored rows. The harness reads that file
from a Windows bind mount and the flush lagged the container's exit, so the console line was computed
early. The stored data is complete and every recorded number is correct; only terminal scrollback
misleads. Noted because someone reading a transcript later would reasonably believe the smaller
number.

---

## The cause of 0/18, measured: the agent cannot afford to write

`write_file` replaces a file **entirely**. Changing five lines therefore means emitting the whole
file as a tool argument, inside `MAX_TOKENS = 16,000` - which caps thinking, text and tool arguments
together.

| case | file | lines | ~tokens to rewrite | share of one reply |
|---|---|---|---|---|
| humanize | `number.py` | 559 | 3,898 | 24% |
| cachetools | `__init__.py` | 776 | 5,818 | 36% |
| markdown | `blockprocessors.py` | 641 | 6,753 | 42% |
| click | `_termui_impl.py` | 945 | 7,933 | 50% |
| more-itertools | `recipes.py` | 1,607 | 11,531 | **72%** |
| **rich** | `console.py` | 2,689 | **25,308** | **158% - impossible** |

One fact accounts for every symptom recorded across 30 real-repository runs:

- **11 writes against 352 reads (1:32)** - the write is enormous or impossible
- **nine runs ended `stop_reason: "length"`** - it begins emitting the file and runs out mid-way
- **the budget experiment changed nothing** - `BUDGET_TOKENS` is per RUN, the wall is `MAX_TOKENS`
  per REPLY. The wrong dial was turned, and 1M tokens bought 281-516k of wandering
- **click sometimes worked** - at 50% the file just fits, and both runs that wrote fixed 5 of 6
  failures
- **`real-rich` is unpassable** by any agent using this toolset, and has been silently contributing a
  guaranteed zero to the denominator

### A v1 decision whose premise expired

> *"A one-line fix means rewriting a whole file, **but these files are 30-80 lines**, and `run_shell`
> gives sed/patch as an escape hatch."*

Correct for practice fixtures. Real files are 559-2,689 lines. The decision was not wrong when made;
its premise stopped being true, which §0 says to state rather than reinterpret.

**This is the third v1 decision to break the same way**, alongside `shrink()` bounding lines rather
than characters (fixture output has short lines) and the scored check having no timeout (fixture
suites always terminate). All three were justified by numbers that only held for 10-file projects.

### How Hermes solves it - checked, not assumed

`tools/patch_parser.py` implements a custom **V4A patch format**: `*** Begin Patch`,
`*** Update File: <path>`, and hunks marked `@@ context hint @@` carrying ` ` context, `-` removal
and `+` addition lines. **The model supplies only changed regions** - editing a 2,689-line file costs
~50 tokens rather than 25,308. It adds `fuzzy_find_and_replace()` for imperfect matches, context-hint
placement for addition-only hunks, and sequential hunk validation so a multi-hunk patch cannot
half-apply.

### What will be built - deliberately smaller

`edit_file(path, old_string, new_string)` with **exact, unique** matching; ambiguous or absent
`old_string` raises with an actionable message. ~15 lines, one file (NFR-601), tool count four -
still under the five-tool break-even that `registry.py` waits on.

**V4A's machinery is deliberately not ported.** Multi-file, multi-hunk, fuzzy matching and sequential
validation are hundreds of lines for capability not yet shown to be needed, and CE-02 is explicit
that a framework earns its place at break-even at the CURRENT scale. If exact matching measurably
fails because the model cannot reproduce strings precisely, the traces will show repeated edit errors
and fuzzy matching is then earned by evidence.

Measured as one cycle: 3 cases x 3 runs, same settings, keep or revert.

---

## Cycle 3 — `edit_file` — the first passes on a real repository

**Change (one):** a fourth tool, `edit_file(path, old_string, new_string)`, exact and unique
matching. `SOUL.md` gains one line naming it — counted as part of shipping the tool, not a second
change, because the prompt lists tools explicitly and would otherwise describe a system that no
longer exists.

**Hypothesis:** `write_file` replaces a file entirely, so a five-line fix meant emitting 559-2,689
lines inside `MAX_TOKENS`. Across 30 runs the agent made 11 writes against 352 reads and scored 0/18.

### Result — clean runs only

| case | baseline | cycle 3 (clean) | evidence |
|---|---|---|---|
| `real-humanize` | 0/3 | 0/2 `compact` x2 | 4->4, no edits landed |
| `real-cachetools` | 0/3 | **2/2 PASS** | 1->0, one `edit_file` each |
| `real-click` | 0/3 | **2/3 PASS** | 6->0 twice (2 and 3 edits); the failure still reached 6->3 |
| **total** | **0/9** | **4/7 = 57%** | first real-repository passes |

**KEPT.** 0/9 to 4/7 is the largest measured delta in this project's history, and the first change
since the model swap to move the number at all.

**57% lands inside the 40-70% band** fixed in advance - the range where a set can actually detect
improvement. This project has never had a set in that band: dev 93%, held-out 97%, multibug 96%, real
repositories 0%. **Tuning cycles are now possible for the first time.**

`real-click` is the sharpest evidence. The same case burned 255,043 tokens in the baseline and stalled
at 6->1; with `edit_file` it fixed **all six failures** in two edits, twice. Even its failing run
reached 6->3. Both passes carry `verdict: compact` - the agent exhausted its budget and passed anyway,
because scoring is by the check command's exit code and never by the agent's own claim.

Both passes verified rather than assumed: `tampered=0`, `write_violations=0`, exactly ONE
`edit_file` call each, applied to `src/cachetools/__init__.py` - the **source**, not the tests - with
zero match errors, and the failing-test count went 1 -> 0.

**Zero edit-match errors across every run that used the tool.** Exact matching was sufficient; the
model reproduced snippets precisely. Hermes's fuzzy matching is therefore still unearned, exactly as
CE-02 requires - if it were needed, the traces would show repeated edit failures, and they do not.

### A rig fault, self-inflicted, found by the tamper check

Three of the eight runs were invalidated and excluded. `real-humanize-2` contained **cachetools'**
test files, though cachetools ran later in the loop - only possible if the two overlapped.

**Cause:** each case-run gets its own container, but they all bind-mount the SAME host workspace.
Wrapping each harness invocation in `timeout 3000` killed the python client while its `docker run`
container kept going; the orphan finished mid-way through the next case and `reset.sh` wiped the
directory the other one was using.

**None of the historical numbers are affected** - 14/15, 29/30, 25/26 and 0/18 each came from a
single sequential invocation with no opportunity to overlap. The fault was introduced by the shell
loop used for this cycle.

**Fixed structurally**, not by a note to self: `await_exclusive_workspace()` refuses to spawn a
case-run while any agent container is still alive, waiting rather than aborting, because an orphan
finishes on its own and a paused suite beats a dead one.

Worth recording that the **tamper check found this**. It was built to catch the agent rewriting the
tests it is judged by; it caught a rig fault instead, which is a better argument for keeping it than
anything it was designed for.

### Standing lesson

**Never wrap the harness in `timeout`.** Killing the client orphans the container, and orphaned
containers corrupt the shared workspace. This joins "never pipe the harness through `tail`" - both
are ways of losing a measurement by managing the harness from outside instead of using its own
controls (`--continue` exists for exactly this).

### Item 18 — `MAX_TOKENS`: measured, and deliberately NOT changed

The plan said to re-examine the per-reply cap only after the edit tool was measured, so it could not
confound the cycle. Measured now:

```
stop_reason "length"
  before edit_file:  4 / 402 model calls   (1%)
  after  edit_file:  0 /  71 model calls   (0%)
```

Truncation has disappeared. The cap is no longer binding, so **`MAX_TOKENS` stays at 16,000** - a
change with no evidence behind it is exactly what the revert rule exists to prevent, and raising it
would inflate cost on every run for no measured gain.

Worth correcting an earlier reading: `length` was never the *dominant* stop reason - 4 of 402 calls.
It was concentrated on the FINAL reply of runs that ended `done`, which is why it looked larger from
the terminal-bucket view. Both statements are true; the second is the one that matters, and cycle 2
was built on the first.

### Item 17 — `real-rich`: no longer impossible, so it STAYS in the set

The case was excluded-in-principle because fixing it required emitting 25,308 tokens into a
16,000-token reply. `edit_file` removes that arithmetic entirely, so the premise was re-tested rather
than the case dropped.

**Result: 0/3, and every run failed for a measured, diagnosable reason.**

| run | verdict | edits | edit errors | failures |
|---|---|---|---|---|
| 0 | compact | 2 | **2** | 1->1 |
| 1 | compact | 3 | **3** | 1->1 |
| 2 | compact | 1 | **1** | 1->1 |

**Every `edit_file` call failed to apply** - six of six - against ONE failed edit across all of
cachetools and click. The case is now hard rather than impossible, which is the right shape for a
scored case, so it stays.

### The failure mode is ambiguity, NOT imprecision - and that matters

Five of the six errors:

```
that text appears 2 times in rich/console.py; it must match exactly once.
```

One was "not found". So the model is **reproducing snippets correctly**; in a 2,689-line file its
chosen snippet simply is not unique.

**This is evidence AGAINST porting Hermes's fuzzy matching**, not for it. Fuzzy matching loosens the
match - in a file that already contains duplicates, that produces more ambiguity, not less, and risks
editing the wrong occurrence silently. The earlier note said fuzzy matching would be earned if
"exact matching measurably fails because the model cannot reproduce strings precisely". It did fail,
but **not for that reason**, so the trigger has not fired.

What Hermes actually uses for this problem is the `@@ context hint @@` that scopes which region a
hunk applies to - a different mechanism from `fuzzy_find_and_replace()`.

### Next candidate cycle, not built now

The error text already says "include more of the surrounding lines", and the model did not recover
from it. The cheap, targeted improvement is to **name where the matches are** - report the line
numbers of each occurrence - so the model knows which region to extend. That is one change, testable
on `real-rich` where the failure is 6/6, and it belongs in its own cycle rather than bolted onto this
one.

---

## Cycle 4 — "name where the matches are" — REVERTED

**Bucket:** `real-rich`, 0/3 with **6 of 6 `edit_file` calls rejected**, five for ambiguity. The
largest concentrated failure in the set.

**Hypothesis:** *"include more of the surrounding lines"* is true but unactionable - in a 2,689-line
file the agent does not know WHICH occurrences collided, so it cannot tell which direction to
extend. Naming the line numbers should make it actionable, exactly as naming `ls` fixed the
directory-read error that once cost three turns a run.

**Change (one):** the ambiguity error reports the line number of each match.

| | pass | edit attempts | edit errors | progress |
|---|---|---|---|---|
| before | 0/3 | 6 | **6/6 (100%)** | 1->1, 1->1, 1->1 |
| after | 0/3 | **2** | 1/2 (50%) | **1->4**, 1->1, 1->1 |

**REVERTED.** Zero delta on the pass rate. The error rate looks better, but on two attempts against
six - and **attempts fell**, which is not a win. One run also regressed 1->4, breaking three
previously-passing tests.

Keeping it would have been rationalising a neutral change on a flattering secondary metric, which is
precisely what the revert rule exists to stop.

### What the traces show instead - three failures, none of them the edit tool

1. **It edits a file it cannot see.** `console.py` is 101,232 characters and `shrink()` caps every
   tool result at **6,000** - about 6% of the file. The agent pages through fragments and edits from
   a partial view.
2. **Plausible but wrong edits.** Run 0 replaced `objects = (NewLine(),)` with `objects =
   (Text(end),)`: reasonable-looking, incorrect, and it broke three passing tests.
3. **No verify-and-revert.** It re-ran the tests, saw 1 -> 4, and left the change in place.

Run 1 did something different again: 18 reads, **zero** edits.

### Checked against Hermes and OpenClaw, not assumed

- **Hermes** scopes hunks with `@@ context hint @@` rather than relying on uniqueness, and
  `tools/file_state.py` warns explicitly on a **"partial read hazard"** - *"was last read with
  offset/limit pagination (partial view)"*. They flag the exact situation `real-rich` is in.
- **OpenClaw** has the same tool shape (exec / read / write / **edit**) and additionally edits inside
  a **managed worktree**, so the main checkout is untouched until the change is reviewed - a bad
  edit is recoverable by construction.

Neither has a mechanism that makes a model choose the RIGHT edit. What they have is better
scaffolding around it, and frontier models behind it.

### The fourth fixture-era premise to expire

`MAX_RESULT_CHARS = 6,000` was sized for 10-file practice projects with short output. On a
2,689-line file it is now the binding constraint, and it joins whole-file writes, `shrink()`'s
line-based bound, and the untimed check as decisions whose stated premises did not survive real code.

**Next cycle target: the read cap, not the edit tool.**

---

## Item 27 — why `real-humanize` fails: reasoning, not tooling

Read before building cycle 5, because it costs nothing and could have redirected it. `real-humanize`
is 0/2 with edits that APPLY (zero match errors) and do not fix (4 -> 4) - a different failure from
`real-rich`, whose edits were rejected outright.

The agent's edit is **1,718 chars in, 2,519 out, applied cleanly**, and is a near-correct
implementation of the SI-prefix carry. One line is wrong:

```
upstream:  exponent += 3 - exponent % 3     <- aligns to the bucket boundary
agent:     exponent += 3                    <- naive
```

For `999.9 V` the exponent is 2. Upstream lands on 3; the agent lands on 5. That changes
`exponent % 3`, which changes `decimal_places` from 2 to 0, so it formats `1 kV` instead of
`1.00 kV`. The carry happens - the precision does not survive it.

It also drops upstream's `abs()` in the rounding test and its `exponent < 30` guard.

**Prediction recorded before cycle 5 runs: the read cap will NOT help this case.** `number.py` is
559 lines and the agent plainly saw enough to write a nearly-correct fix; nothing here is a
partial-view failure. If `real-humanize` improves in cycle 5, the explanation is wrong and the
result is noise.

This is the first failure in the set attributable to **numerical reasoning** rather than to a tool,
a budget or a rig fault. No tooling change addresses it.

---

## Cycle 5 — `read_file` returns a contiguous window — PARTIAL, NOT YET DECIDED

**Change (one):** `read_file` sizes its own window so the result fits under the existing cap,
instead of letting `shrink()` take head+tail out of it afterwards.

`shrink()` exists for UNEXPECTEDLY large output. A paged read is the opposite - a deliberate,
bounded request - and eliding its middle deletes exactly what was asked for.

Measured on `rich/console.py` (101,228 chars, 2,689 lines):

| | before | after |
|---|---|---|
| lines per read | 50 (30 head + 20 tail) | **164 contiguous** |
| middle elided | yes | no |
| reads to see the whole file | 54 | **17** |
| result size | 6,000 cap | 5,839 |

**NFR-104 is untouched** - results still fit the cap. Only the SHAPE changed. No spec amendment was
needed, which is why this was chosen over raising `MAX_RESULT_CHARS`.

### Result so far: INCOMPLETE - 2 of 9 runs

```
real-rich  run0 PASS 1->0   (18 turns, 8 reads, tampered=0)
real-rich  run1 FAIL 1->1
```

Stopped early at the user's request; `real-click` and `real-cachetools` - the regression guards -
have NOT run.

**`real-rich` has now passed for the first time.** It was 0/3 with `edit_file` alone and 0/3 again
with cycle 4's ambiguity fix. The passing run found and fixed the bug in **8 reads**, against 15-18
in the failing runs before it - the shape the hypothesis predicted.

**This is not yet a kept change.** 1 of 2 is not a result: the project's own rule is 3 runs per case,
and the guards exist precisely to catch the case where the explanation is wrong.

### Pre-registered predictions, still unchecked

- `real-rich` improves - **so far consistent** (1/2, was 0/3 twice)
- `real-cachetools` and `real-click` do NOT move - their files are 776 and 945 lines and already
  mostly fit. **If they move, the explanation is wrong and the result is noise.**
- `real-humanize` does NOT improve - its failure is numerical reasoning (Item 27), not partial view.

### To resume

```
python eval/harness.py --case real-rich       --runs 3
python eval/harness.py --case real-click      --runs 3
python eval/harness.py --case real-cachetools --runs 3
```

### A transient provider 404 was being scored as a case failure

Found mid-cycle: a run died at turn 0 with 0 tokens on `NotFoundError`, and the same model answered a
probe minutes later. `NotFoundError` sat in neither `RETRYABLE` nor `FATAL`, so it fell through to
"a crashed agent IS a real result" and was recorded as a **failed case** - the exact thing the
standing rule forbids, because a run that never reached the model measured nothing.

The fix landing mid-run made the contrast visible in one case:

```
real-click run0  404 -> status=ok,      scored FAIL   (before the fix)
real-click run2  404 -> status=blocked, excluded      (after the fix)
```

Identical failures, classified differently by timing alone.

**Classified retryable, not fatal.** A genuinely wrong model name fails every attempt and is then
excluded as blocked - visible, costing three fast 404s. A transient blink recovers on the first
retry. Treating it as fatal would abort a whole scored suite over one hiccup.

### What the 404s actually were - captured, not guessed

```
404  NotFoundError      (EMPTY body)
500  "Internal error while making inference request"   nvcf-worker-service
503  "Service temporarily overloaded"
```

**NVIDIA's inference backend is overloaded**, and says so in two of the three. The 404s carry an
**empty body**, which is a load balancer routing to a worker with no model loaded - not "your model
does not exist". Three error codes, one root cause.

Not the key (a bad key gives 401, and none appeared), not the model name (it is in the catalogue and
answers when a worker is free), not our code (nothing reaches it). A case needs ~20 sequential model
calls, so at ~60% success per call a full run rarely completes even though isolated probes look
tolerable - which is why single probes and whole runs disagree so sharply.

### Cycle 5 remains UNDECIDED

The re-run was stopped after `real-rich` run 0 blocked twice. Spending the day's quota during a
backend outage buys another four-run sample, which is the very problem the re-run existed to fix.

Standing, excluding runs that measured nothing:

| case | before | cycle 5 | role |
|---|---|---|---|
| `real-rich` | 0/3 | **1/2** | target - moved |
| `real-click` | 2/3 | 1/1 | guard - cannot falsify at n=1 |
| `real-cachetools` | 2/2 | 1/1 | guard - cannot falsify at n=1 |

The change stays in the tree, committed and **explicitly unverified**. Re-run when the backend
recovers: `--case real-rich / real-click / real-cachetools --runs 3`.

## Cycle 5 re-run — `real-rich` 0/3 -> 3/3, guards unmeasured

| case | before | after | role |
|---|---|---|---|
| `real-rich` | **0/3** (twice) | **3/3** | target |
| `real-click` | 2/3 | **0/0, 9 blocked** | guard - no data |
| `real-cachetools` | 2/2 | **0/0, 9 blocked** | guard - no data |

All three `real-rich` passes clean: `tamper=0`, `write_violations=0`, every run 1 -> 0.

```
run0 PASS  20 turns  10 reads  1 edit,  0 errors
run1 PASS  19 turns  10 reads  1 edit,  0 errors
run2 PASS  30 turns  16 reads  2 edits, 1 error
```

Against the same case before the change: 15-18 reads and **6 of 6 edits rejected**. Two runs now
find and fix a bug in a 2,689-line file with **ten reads and a single edit**.

**The ambiguity failures disappeared without being addressed.** Cycle 4 tried to fix them directly by
naming match locations and was reverted at 0/3. They were a *symptom* of the partial view: an agent
that can see contiguous code picks a unique snippet by itself. Fixing the cause removed the symptom
that the direct fix could not.

### KEPT, with the caveat stated rather than buried

The target moved decisively and is clean. **Both regression guards produced zero scored runs** - 18
blocked attempts between them, as NVIDIA's backend degraded again mid-cycle.

So the claim "cases that already fit under the cap are unaffected" is **still unverified**. It is
plausible - `read_file` only narrows when a window would overflow, and 776/945-line files rarely do -
but plausible is not measured, and this project has a standing rule about the difference.

Kept rather than reverted because the evidence that exists is strong and one-directional: 0/3 twice,
then 3/3, on the hardest case in the set, with the predicted mechanism visible in the traces.

**Owed: re-run `real-click` and `real-cachetools`, 3 runs each, when the backend is stable.** Until
then the guard claim is an open question, not a result.

### Provider conditions during this cycle

19 blocked runs against 3 scored. Errors captured directly: empty-bodied 404s, `500 "Internal error
while making inference request"` from `nvcf-worker-service`, and `503 "Service temporarily
overloaded"`. Not the key, not the model, not the code - free-tier capacity. Blocked runs are
excluded rather than scored, so the data stays honest; there is simply far less of it per hour.

---

## Provider cleanup — any OpenAI-compatible endpoint, one key variable, one preflight

No measurement: nothing here can change agent behaviour. Done while the backend was down, which is
the right work for that time.

The adapter already supported any OpenAI-compatible provider - `agent/provider.py` is the only file
that imports a provider SDK, and it dispatches on one variable. Three warts made that hard to use.

**1. The names lied.** `NIM_BASE_URL` / `NIM_MODEL` were what you set for OpenAI, OpenRouter or Groq.
Now `OPENAI_BASE_URL` / `OPENAI_MODEL` - the protocol being spoken, not the vendor - with the NIM_*
names kept as fallbacks so existing `.env` files keep working. Verified both directions: old names
alone still resolve; new names win when both are present.

**2. The key variable was wrong-shaped.** It read `NVIDIA_API_KEY` first, so an OpenRouter key went
into a variable named for a different vendor. Now `AGENT_API_KEY` -> `OPENAI_API_KEY` ->
`NVIDIA_API_KEY`, provider-neutral first.

**3. Nothing checked the endpoint before a run.** `--check-provider` sends ONE tool-calling request
and reports what came back. This matters more than it looks: the spec forbids parsing tool calls out
of free text, so a model that will not emit a well-formed call has **no fallback** - it fails
mid-run, after spending quota. One open-weight model tried here leaked raw `<tool_call>` markup as
message text and the run ended there. The check reports that case specifically rather than just
"200 OK".

```
$ python eval/harness.py --check-provider
provider : nvidia
endpoint : https://integrate.api.nvidia.com/v1
model    : nvidia/nemotron-3-super-120b-a12b
tool call: OK - run_shell({"command": "ls -la"})
verdict  : USABLE
```

The egress allowlist is derived from the base URL, so pointing at a new provider moves the proxy rule
automatically - there is no firewall entry to remember.

**Unchanged, and restated in `.env.example`:** switching models invalidates every existing baseline.
It is a different measurement, not a tuning delta - this project's dev score once moved 4/15 -> 14/15
on a model swap alone, with no code change.

### A watcher that would have waited three hours for nothing

While the backend was at 0/8 a script was written to poll health and launch the guards on recovery.
Its probe mounted `$HOME/.claude/jobs/.../tmp` into the container - a path Docker on Windows does not
resolve - so every probe returned empty and printed `health ?/6`. It would have polled until its
three-hour deadline and never fired, while the backend recovered underneath it.

Caught only because `--check-provider` was run by hand minutes later and succeeded. **A watcher whose
failure mode is silence needs its probe verified before it is trusted** - the same lesson as
`State.Running: true` on a proxy that could not resolve DNS. The script was deleted rather than
fixed; the guards were run directly.

### Cycle 5 guards — run at last, and they hold

The backend recovered, so the two regression guards finally produced data.

| case | before | after | role |
|---|---|---|---|
| `real-rich` | **0/3** (twice) | **3/3** | target |
| `real-click` | 2/3 | 3/3 | guard |
| `real-cachetools` | 2/2 | 2/3 | guard |

**Neither guard moved outside noise, in either direction.** `click` gaining a run and `cachetools`
losing one are single-run differences at n=3 - exactly the wobble expected of cases whose files (945
and 776 lines) already mostly fit under the cap. The prediction was "these should not move", and
nothing here falsifies it.

The mechanism shows even where the score does not: `click` used **11 reads in all three runs**
against 12-17 before, and `cachetools`' passes took 7 and 9. Its one failure made **zero edits** -
the older write-reluctance, not an edit that went wrong.

**CYCLE 5 KEPT**, now on evidence rather than on a caveat.

### Read this before quoting a set-level number

`0/18 -> 8/9` is **wrong** and was briefly stated that way. Three honest limits:

1. **Three of six cases.** `real-markdown` and `real-more-itertools` have never run with either the
   edit tool or contiguous reads. `real-humanize` is known-failing on arithmetic (Item 27).
2. **Those three are the cases that were tuned against.** Traces were read, hypotheses formed and
   changes measured on `rich`, `click` and `cachetools` all day. That is selection, and it is the
   same overfitting risk the held-out set exists to catch on the fixture side.
3. **The gain is one case.** Strip `real-rich` out and the change shows nothing: +1 run on `click`,
   -1 on `cachetools`. The combined figure hides that.

What is solid: **`real-rich` went from impossible to reliable**, clean every time, with the predicted
mechanism visible in the traces. The set-level number is unknown until Item 25 runs.

---

## Phase K — two writable roots, held by the kernel instead of asserted after the fact

**Not a tuning cycle.** No prompt, tool or loop change; the pass rate is a regression guard here,
not a measurement. What changed is where the agent is allowed to write.

### The defect, found while planning the phase

`spawn()` mounted the project at `/app` writable, and `--read-only` does not cover bind mounts. The
probe, run before anything was changed:

```
WRITABLE   /workspace/.probe
WRITABLE   /app/.probe
WRITABLE   /app/eval/fixtures/.probe
WRITABLE   /app/eval/tasks.jsonl.probe
refused    /usr/local/.probe   (Read-only file system)
```

So the agent could write to the harness that scores it, to `tasks.jsonl`, and to the fixtures that
decide whether it passed. **Nothing ever did** — all 30 Phase I traces show zero access to `/app` —
but two things were wrong regardless:

1. The guard was **post-hoc**. `restore_protected_tests()` repairs damage after the run; a kernel
   refusal prevents it. Repair is the weaker claim and the docs made the stronger one.
2. A successful write to `/app` **would not have registered as a violation at all**, because
   `count_write_violations()` looked for `Read-only file system` errors that could never occur there.
   The boundary check was blind to exactly the region it most needed to watch.

### What changed

| | before | after |
|---|---|---|
| project tree | `-v REPO:/app` (writable) | `-v REPO:/app:ro` |
| traces | inherited `/app`'s writability | `-v REPO/eval/runs:/app/eval/runs` |
| agent state | `/app/.agent/state.db` | `/state/state.db`, its own mount |
| scored agent home | n/a | **blank per case-run** |
| violation report | a count | a count **and the paths** |

`AGENT_HOME` moved to `/state` in the `Containerfile` and `config.py`. It had satisfied "outside the
workspace" by living in the project tree — which only worked because the project tree was writable,
so the fix to one was the fix to the other.

**Per case-run, blank.** Phases M and N put memory and skills in the agent home. A memory carried
from case 1 into case 2 is the same contamination that already forced one container per case-run,
where a shared container left `missing-dep`'s package installed and the repeat passed without the
agent doing anything. The interactive CLI keeps a persistent home; only the scored suite starts
blank, because only the scored suite is a measurement.

### Proof, both directions, zero quota

```bash
MSYS_NO_PATHCONV=1 docker run --rm --network none --read-only --tmpfs /tmp:exec \
  -v "$(pwd -W):/app:ro" -v "$(pwd -W)/eval/runs:/app/eval/runs" \
  -v "$(pwd -W)/eval/workspace:/workspace" -v "$(pwd -W)/.agent/homes/_probe:/state" \
  personal-agent bash -c 'for p in /workspace /state /app/eval/runs /app \
      /app/eval/fixtures /app/eval/tasks.jsonl.x /app/agent/tools.py.x /usr/local; do
    if touch "$p/.probe" 2>/dev/null || touch "$p" 2>/dev/null
      then echo "WRITABLE $p"; else echo "refused  $p"; fi; done'
```

```
-- must be writable
WRITABLE   /workspace/.probe
WRITABLE   /state/.probe
WRITABLE   /app/eval/runs/.probe
-- must be refused
refused    /app/.probe                    (Read-only file system)
refused    /app/eval/fixtures/.probe      (Read-only file system)
refused    /app/eval/tasks.jsonl.probe    (Read-only file system)
refused    /app/agent/tools.py.probe      (Read-only file system)
refused    /usr/local/.probe              (Read-only file system)
```

Both directions, because a probe that only checks refusals passes just as well on a container that
mounts nothing at all.

### K4 — per-case egress, and the obvious implementation that does not work

A case may now declare `"egress": [...]` on its `tasks.jsonl` row; the allowlist is derived per
case-run and recorded in the manifest. No case declares any today, so every current number is
measured against the model host alone, exactly as before.

**SIGHUP was measured, not assumed, and it does not reload the filter.** With `example.com` already
present in the mounted filter file, tinyproxy refused it identically before and after the signal:

```
before HUP (file already widened):  curl: (7) CONNECT tunnel failed, response 403
after  HUP:                         curl: (7) CONNECT tunnel failed, response 403
proxy log: Proxying refused on filtered domain "example.com"
```

The mounted file *does* propagate from host to container — that was checked separately, and it is
the assumption that would otherwise have been blamed. tinyproxy simply reads the filter once, at
startup. So `apply_allowlist()` recreates the proxy instead. Two seconds, and only when the list
actually changes.

A widening that silently fails is harmless on its own — it fails closed. The damage would have been
in the manifest, which would have recorded the allowlist that was *asked for*: a row stating a
condition that was never true.

With recreation, both directions hold:

```
case declares example.com:      example.com -> 200,  an unlisted host -> refused
next case declares nothing:     example.com -> refused
```

A proxy left running by an *earlier invocation* is now recreated too. What a running proxy enforces
is not observable from outside — only the file is, and the file is not what it loaded — so the only
way to know is to have started it.

### A row that claimed a condition nobody checked

Found while probing K4. Every trace row carried `"egress": "restricted"` from

```python
"egress": os.environ.get("AGENT_EGRESS", "restricted"),
```

and **nothing anywhere ever set `AGENT_EGRESS`.** Every row ever written asserted restricted egress,
including runs deliberately made on `AGENT_NETWORK=bridge`, and including a `--network none`
preflight during this phase. `spawn()` now sets it to the actual allowlist, and the in-container
fallback says `UNKNOWN` rather than `restricted` — **a default that asserts the safe answer is how a
row comes to claim a condition nobody checked.**

This does not invalidate the existing numbers: those runs *were* egress-restricted, verified through
the proxy at the time. It invalidates the row as *evidence* of it, which is the property that
mattered.

### Tests

182 offline tests pass under the new mounts — no API key, no network, read-only root, `/tmp` a
tmpfs. Nine are new: the violation paths, an old row without the new field, the agent home being
outside the workspace, `reset.sh` leaving it alone, a blank home per case-run, and per-case egress
widening only its own case.

`pytest` warns twice that it cannot write `/app/.pytest_cache` — expected, and the point. The scored
check runs in `/workspace` and is unaffected.

### K5 — the regression guard: 14/15, case for case

**2026-08-21, `nemotron-3-super-120b-a12b`, 3 runs per dev case, 0 blocked.**

| case | Phase K | baseline (2026-08-19) | verdicts now | tokens (med) |
|---|---|---|---|---|
| `fix-import` | **3/3** | 3/3 | done x2 stuck x1 | 26,600 |
| `off-by-one` | **3/3** | 3/3 | done x3 | 31,368 |
| `broken-fixture` | **3/3** | 3/3 | done x2 stuck x1 | 21,784 |
| `missing-dep` | **3/3** | 3/3 | done x3 | 10,854 |
| `add-endpoint` | **2/3** | 2/3 | done x1 stuck x2 | 41,369 |

Not just the same total — **the same case-by-case pattern, including which single case fails.** A
sandbox change that broke something would have moved a case, not shaved the total.

`missing-dep` is the one that mattered most and it is 3/3: its fix is a plain `pip install`, so it
proves `PIP_USER` / `PYTHONUSERBASE` still resolve to the `/tmp` tmpfs now that a third and fourth
mount exist and the project is read-only.

Trust checks, all verified rather than assumed:

```
rows                 15          blocked           0
tampered              0          write violations  0   (paths: none)
models               ['nvidia/nemotron-3-super-120b-a12b']
egress (per row)     ['integrate.api.nvidia.com']
manifest network     personal-agent-egress
```

**This is the first run in the project's history where the egress on a row is a recorded fact rather
than a default string.** Ceilings held: median 26,600 / 60,000 tokens, largest single result
4,604 / 6,000 chars.

### Found while checking the above, NOT fixed — `failing_tests()` reads a collection error as zero

The table shows `fix-import` starting at `0` failures, which would mean the fixture was green before
the agent touched it — a case scored as a pass nobody earned. It is not. The untouched fixture was
re-run directly:

```
ERROR tests/test_parser.py
E   ImportError: attempted relative import beyond top-level package
13 passed, 1 error in 0.24s
EXIT=1
```

The suite genuinely fails. `failing_tests()` looks for `N failed`, finds none, falls through to
`13 passed`, and returns 0 — so a **collection error reads as a green suite.** Its own docstring says
it returns 0 only for a green suite precisely so that "no failures" and "could not tell" stay
distinguishable, and here they do not.

**The score is unaffected**: `pass` comes from the check's exit code, never from this count. Only
the partial-progress column is wrong, and only where a case fails at import rather than at assert -
which is why it never showed on the real-repository set the column was built for.

**Left unfixed deliberately.** It is not a Phase K defect and fixing it here would change the dev
table in a way unrelated to the sandbox, muddying the regression comparison this run exists to make.
It is one regex, and it should be its own change.

---

## Phase L — MCP: one server, and a capability claim that did not survive its own baseline

**Headline: MCP bought efficiency, not capability.** The web split scored 18/18 *without* MCP, so
the thing this phase was built to add turned out to already exist. What it did buy is a **50% cut in
turns and a 38% cut in tokens**, which is worth having and is not what was planned for.

### Three measurements taken while planning, before any code

They reshaped the phase, and each would have cost a week of quota to discover afterwards.

**1. Prompt caching is not happening.** All 15 rows of the Phase K regression run report
`cache_read_tokens: 0`. The `SCHEMAS` comment in `tools.py` — *"tools render first in the prompt, so
reordering them invalidates the entire prompt cache"* — is sound reasoning that buys nothing here.
Every schema is paid in full, on every request, at a mean of **9.1 model calls per run**.

**2. Tool schemas were already 23% of a run.** Four built-ins are 1,997 chars ≈ 665 tokens; × 9.1
calls ≈ 6,050 tokens against a median run of 26,600.

**3. The ecosystem is not reachable.** `node ABSENT, npx ABSENT, uvx ABSENT`, `import mcp` fails, and
`/etc/pip.conf` is `no-index`. The npx-based majority of public MCP servers cannot run in this image,
and nothing installs at run time.

Together these killed the ROADMAP's stated strategy — *"inherit an ecosystem instead of hand-writing
37 tools"*. At a real server's ~254 tokens/tool, **24 exposed tools would add ~61,500 tokens/run and
breach NFR-402's 60,000 ceiling on schema alone.** Breadth is the most expensive thing this project
could buy, and it is charged per turn whether a tool is used or not.

So Phase L shipped **one** server, and `config.MAX_SCHEMA_CHARS = 6_000` now refuses to start a run
that exceeds the budget — loudly, naming the overrun, because a budget that can be exceeded with a
printed warning is not a budget.

### The pin, measured not assumed

`mcp==2.0.0` + `mcp-server-fetch==2026.8.18` fails with `ResolutionImpossible`: the server declares
`mcp<2,>=1.29.0`. Pinned to **1.29.0**. The server also prints *"node executable not found, reverting
to pure-Python mode"* on every start — expected, and the reason it was chosen: it degrades rather
than failing.

### `registry.py` was NOT created, and that is the finding

§12 deferred it with an unusually specific trigger: *"Break-even against hand-written schemas is five
tools; v1 has three. **Add at tool six.**"*

Counted honestly: four built-ins (`edit_file` landed during the real-repo work) plus `fetch` is
**five**. Five is not six. The file was not created, and the merge lives in `tools.toolset()` — nine
lines with two callers, which is what CE-01 asks for. **A deferred layer with a numeric trigger does
not get to fire because the phase that would use it has arrived.**

### The zero baseline was not zero — the most important result here

The plan said to record the web split's zero baseline *before* the capability, and to **verify it was
zero for the right reason**. It was not zero. It was **18/18**.

`web-release` run 0, with `AGENT_MCP=off`:

```
run_shell  | curl -s http://fixture-web/release.html      <- not in the image
run_shell  | which wget                                    <- not in the image
run_shell  | python3 --version
run_shell  | python3 -c "import urllib.request; ..."       <- worked
write_file | answer.txt
read_file  | answer.txt
```

`run_shell` plus Python's `urllib` reaches the web already, because `spawn()` sets `HTTP_PROXY` in the
container and urllib honours it. The agent found that in four calls, in every case, in all 18 runs.

**Had the baseline been skipped as "derivable zero", this phase would have shipped a false capability
claim** — and it would have been unfalsifiable afterwards, because the split would have looked like it
went 0 → 18. That is exactly what the measurement-before-capability rule exists to catch, and it is
the first time in this project that it has actually caught something.

### The comparison that Phase L can honestly make

Same 6 cases, 3 runs each, one flag apart. The kill switch is what makes this a controlled
comparison rather than two separate measurements.

| | MCP off | MCP on | delta |
|---|---|---|---|
| pass | 18/18 | 18/18 | — |
| turns (mean) | 6.2 | **3.1** | **−50%** |
| tokens (median) | 11,528 | **7,293** | **−37%** |
| tokens (total) | 217,213 | **133,951** | **−38%** |
| tool calls (mean) | 6.2 | 3.1 | −50% |
| tool errors | 0 | 0 | — |
| schema per request | 1,997 | 3,159 | **+58%** |

**The schema got 58% more expensive per request and the run still got 37% cheaper.** The saving is
not subtle and it is not in the model: it is four discovery calls that stop happening. `fetch` also
returns extracted text rather than raw HTML, which is why the largest single result fell from 1,081
to 234 chars.

Trust checks on both: 18 rows, 0 blocked, 0 tampered, 0 write violations, one model, and
`mcp: []` versus `mcp: ["fetch"]` recorded per row so the two can never be confused.

**This was also the first scored use of Phase K's per-case egress.** Every row records
`fixture-web,integrate.api.nvidia.com` — the case declared the host, and the allowlist was narrowed
back afterwards.

### The gate, and one gap that was real

`PATH_ARGS` guarded `path`, `file`, `cwd` — the argument names *this project's own tools* use. That
was sufficient only while the tool set was closed. A server calling its argument `filename`,
`directory`, `destination`, `target` or `output` would have walked straight past the workspace check.
Widened, and the bypass is now a test rather than an assumption.

`url` is deliberately **not** in the list: running one through the workspace check resolves
`https://x/y` into a subdirectory of the workspace and approves it, and a check that produces a
confident wrong answer is worse than no check.

`policy.register()` refuses to default an unclassified tool to `read` — it records `destructive`, so
such a tool prompts interactively and is refused unattended. That is Phase K's `AGENT_EGRESS` lesson
applied from the other side: a default that asserts the safe-looking answer hides what it should
surface.

### Tests

**202 offline**, up from 182 — no API key, no network, read-only root, and **without the `mcp`
package installed**, because `agent/mcp.py` imports the third-party client inside the coroutine that
needs it rather than at module level.

### The dev regression guard — no regression, and the cost of carrying a tool you do not use

The +58% schema is charged on the bug-fixing split too, where nothing ever calls `fetch`. That is
where an unearned tool shows up as pure cost, so this is the guard that mattered.

| dev suite | Phase K (no MCP) | Phase L (MCP on) |
|---|---|---|
| pass | 14/15 | **15/15** |
| total tokens | 383,489 | 396,025 (**+3.3%**) |
| median tokens | 26,600 | 27,266 (+2.5%) |
| mean turns | 8.4 | 8.0 |
| schema per request | 1,997 | 3,159 (+58%) |
| tampered / write violations | 0 / 0 | 0 / 0 |

**15/15 is NOT a Phase L improvement and must not be reported as one.** The extra pass is
`add-endpoint`, which scored 2/3 in the 2026-08-19 baseline and 2/3 again in Phase K. It is the
suite's known-flaky case and one run at n=3 is inside its own variance. Nothing in MCP could plausibly
help a case with no network in it. **What the guard establishes is the absence of a regression, which
is all it was ever asked to establish.**

The cost answer is the useful one: **a tool you never call costs about 3% of a long run**, not 58%.
The schema is a fixed per-request charge and bug-fixing runs are long, so it dilutes. On the web
split, where the tool IS called, the same exposure saved 37%.

That is the whole trade, and both halves are now on the row: `schema_chars` and `mcp` are recorded
per run, so no future comparison can mix a 4-tool measurement with a 5-tool one by accident.

### Phase L scoreboard

| | result |
|---|---|
| exit criterion (capability) | **not met, and the criterion was wrong** — the agent already had the web |
| exit criterion (boundary) | met: server inside the sandbox, unknown tools denied, 0 violations |
| exit criterion (cost) | met: NFR-402 never approached; budget enforced at 6,000 chars |
| dev regression | met: no regression |
| **what was actually bought** | **−50% turns, −37% tokens on web tasks; +3.3% on everything else** |

**Kept.** Not for the reason it was proposed.

---

## Phase M — Memory: 0/18 without it, 15/18 with it, and 40% cheaper

**The cleanest result this project has produced.** A benchmark that scores zero before the feature
and moves after it is the thing Phases J through L kept failing to build — and it very nearly did
not happen here either.

**Scope was chosen deliberately and against advice:** episodic recall *and* a durable profile. The
profile was flagged as the harder half — a second store, with no obvious pass/fail — and taken
anyway. So it was given its own deterministic score rather than shipped on vibes.

### The baseline, which is the whole reason this number means anything

Phase L was meant to take a web split from 0 to 18 and the baseline came back **18/18** — the agent
could already do it. That result made this one non-negotiable, and the trap was verified to be
present here before the run, not assumed absent:

```
gate verdict: ('auto', 'run_shell classified write')
run_shell(command='echo "deploy key kx-9920" > /state/notes.md && cat /state/notes.md')
  -> exit 0, "deploy key kx-9920"
```

`run_shell`'s only argument is `command`, which is not in `PATH_ARGS`, so no workspace check applies
to it — and Phase K made `/state` writable. **The agent could have passed this benchmark with no
memory layer at all.** It was left open on purpose: patching it before measuring would have destroyed
the evidence of whether it mattered.

It did not fire. **memory OFF scored 0/18**, and the traces say why:

```
session 1:  write_file deploy_key.txt      <- its instinct WAS to persist...
                                              ...into the workspace, which reset.sh wipes
session 2:  grep -r "deploy" .
            env | grep -i deploy
            find . -type f  (x4)
            -> nothing. guessed.
```

The agent tried to persist — to the workspace, not to `/state`. **Resetting the workspace between
sessions is what made this benchmark valid**, and that was a design decision taken before the data
existed, not a lucky one.

**The two families fail differently, and that is worth keeping:**

| family | turns | tokens | verdicts |
|---|---|---|---|
| `recall-*` (a fact) | 10/10/10, at the cap | ~23,000 | mostly `stuck` |
| `profile-*` (a habit) | 2-5 | ~6,000 | all `done` |

Asked for a fact it does not have, the agent **thrashes**. Asked to honour a preference it was never
told, it finishes fast and **confidently wrong**. One missing capability, two symptoms.

### The result

Six cases, three runs each, two sessions per case-run, one flag apart.

| | memory OFF | memory ON |
|---|---|---|
| `recall-*` (facts) | 0/9 | **9/9** |
| `profile-*` (habits) | 0/9 | **6/9** |
| **total** | **0/18** | **15/18** |
| median tokens | 8,744 | **6,220** (−29%) |
| total tokens | 233,544 | **139,347** (−40%) |
| mean turns | 5.4 | **2.7** (−50%) |
| runs ending `stuck` | 7 | **0** |

**Memory was budgeted as a cost and came out a saving.** Injected context is charged on every request
(697 chars at most, and `memory_chars` is on every row), and the run still finished 40% cheaper,
because the thrashing stops. Every `stuck` verdict disappeared.

Trust checks both ways: 18 rows each, 0 blocked, 0 tampered, 0 write violations, one model,
`memory: false/true` and `sessions: 2` recorded per row.

### The mechanism, read from the traces rather than assumed

```
session 1:  write_file ORIGIN.txt          <- still tries the workspace first
            remember {"note": "The user has a standing rule that every file
                       I create must start with ORIGIN: quartzite-desk"}
session 2:  write_file notes.txt           <- with the line, unprompted
```

**The agent adopted `remember` on its own.** `prompts/SOUL.md` never mentions it — it was discovered
from the tool schema alone, exactly as `fetch` was in Phase L. Both stores are live and it picks
between them situationally: `profile-marker` went through `AGENT.md`, while `profile-units` recalled
the rule from an episode with `AGENT.md` left empty.

### A fixture defect, and the fix recorded as a separate cycle

`profile-units` scored 0/3. Not a memory failure — all three runs wrote **`200cs`**, while the check
was `grep -qF "200 cs"`. The conversion (two seconds → 200 centiseconds), the unit and the label were
all correct. **The case was measuring whitespace.**

This is the cost of deterministic scoring, taken deliberately over an LLM judge: a judge would have
passed it, but a judge whose agreement with a human has never been measured is an opinion with a
number printed on it.

The check was **not** touched during the run. It was amended afterwards, on request, to
`grep -qE "200 ?cs"` and **re-verified in both directions before being trusted**:

```
untouched                 -> fail        '2 seconds'  -> still fails
'200cs'                   -> pass        'the build took 2s' -> still fails
'Build took 200 cs'       -> pass        '20 cs'      -> still fails
'build took 200cs total'  -> pass        '2000cs'     -> still fails
```

The last column is the one that matters: a loosened check must not make the case passable by writing
any number at all.

**Both numbers stand on the record. 15/18 is the score as measured. The amended-check re-run is a
separate, later cycle, made after seeing the data, and is labelled as such** — a number that improved
after its scorer was adjusted has to say so.

### `registry.py` — created, but not the thing §12 describes

§12's trigger is *"add at tool six"*. Phase L counted five and correctly declined. `remember` is the
sixth, so it fires.

**The `@tool` decorator it names is still not built, and the arithmetic says so.** §13 costed that
machinery at ~25 lines plus ~5 per tool against ~8 per tool written out, which breaks even above
eight *hand-written* tools; there are five, because `fetch`'s schema arrives from the server. The
stronger reason is that the descriptions are load-bearing: the text in `edit_file`'s schema coaches
the model on choosing a unique snippet, and that coaching is what took real repositories from 0/9 to
4/7. Deriving a schema from a signature either loses it or takes it as a decorator argument, at which
point the schema has been written anyway.

What the file **does** own is a real misplacement the sixth tool exposed: `check_budget()` bounds the
whole tool set and was living in `agent/mcp.py`, which stopped being right the moment a second source
of tools appeared.

### The prompt was NOT widened, and the result argues it did not need to be

The plan's M0 said to widen `prompts/SOUL.md`, which opens *"You fix broken code"*. It was left
alone: widening it in the same cycle as adding memory is two variables, and the Iron Law forbids
that. The agent then discovered and used `remember` with no prompt support at all.

**The evidence for a future prompt cycle is now on the record, though.** In session 1 the agent
runs `which git`, `pytest -q` and `git status` when it has simply been *told a fact* — it treats a
conversational statement as a repository to investigate. That costs turns and is exactly what a
widened prompt would fix. It is a separate cycle with its own measurement.

### Tests

**225 offline**, up from 202 — no API key, no network, read-only root. The suite caught a real
defect introduced here: `finish` now writes episodes, so the node tests were about to read and write
the **real** agent home. `tests/conftest.py` redirects `MEMORY_DB` and `PROFILE` alongside
`STATE_DB`, and a test that passes or fails depending on an unrelated session is exactly the failure
that redirect prevents.

The re-run, on the amended check: **`profile-units` 3/3**, 0 blocked, 0 tampered, 0 write violations.
That puts the recall split at an amended **18/18** against a **0/18** baseline — with the amendment's
provenance stated above, and the as-measured 15/18 left standing beside it.

### The dev regression guard

| dev suite | K (4 tools) | L (+mcp) | M (+memory) |
|---|---|---|---|
| pass | 14/15 | 15/15 | **14/15** |
| total tokens | 383,489 | 396,025 | **444,481** |
| median tokens | 26,600 | 27,266 | 31,582 |
| mean turns | 8.4 | 8.0 | 8.6 |
| schema chars | 1,997 | 3,159 | **3,553** |
| memory chars | 0 | 0 | **0** |
| tamper / violations | 0/0 | 0/0 | **0/0** |

**No regression** - 14/15 is the original baseline, and L's 15/15 was already recorded as noise on
`add-endpoint` rather than an improvement.

`memory_chars: 0` across the whole dev suite is correct and worth stating: scored case-runs get a
FRESH agent home (Phase K), so on a single-session case there is never anything to recall. Memory's
cost on coding work is therefore the `remember` schema alone - 394 chars, ~131 tokens per call, which
accounts for roughly 18k of the 61k rise. **The rest is run-to-run variance at n=3**: mean turns moved
8.0 -> 8.6 and `add-endpoint` alone burned 43k. Not attributed further than that.

### Phase M scoreboard

| | result |
|---|---|
| recall benchmark built before the feature | met - and the baseline was 0/18, not a derivable zero |
| episodic recall | **0/9 -> 9/9** |
| the profile, given a real pass/fail | **0/9 -> 6/9 as measured, 9/9 on the amended check** |
| cost reported beside recall | met - and it was a 40% SAVING, not a cost |
| dev regression | met - 14/15, unmoved |
| vectors (FR-408) | **not built.** Keyword recall was measured and did not fall short |

**Kept.**

---

## Phase N — Skills: on-demand knowledge, 0/18 without it, 17/18 with it

**Skills are on-demand knowledge documents in the agentskills.io layout**, loaded through
progressive disclosure. Phase N builds LOADING only; authoring is Phase O. You cannot measure whether
a self-written skill helps until you can measure whether *any* skill helps, and with a hand-written
skill a failure has one suspect instead of three.

### Why disclosure matters more here than almost anywhere

Phase L measured `cache_read_tokens: 0` on every row of a scored run: everything in the prompt is
re-sent and re-paid on every turn, and four tool schemas were already ~23% of a median run. A
knowledge library injected wholesale would be ruinous. So:

```
Level 1  always loaded   name + description   1,224 chars for 8 skills
Level 2  on demand       the SKILL.md body    paid only when opened
Level 3  on demand       bundled files        codes.md, template.conf
```

### The result

Six cases, three runs each, one flag apart. **The control is memory ON, skills OFF** - not "nothing".
`memory.context_for()` already injects "commands that worked" from past sessions, so skills had to
beat that rather than beat zero.

| | skills OFF | skills ON |
|---|---|---|
| pass | **0/18** | **17/18** |
| load rate (the right skill) | — | **17/18** |
| loaded the WRONG skill | — | **0** |
| loaded nothing | — | 1 |
| median tokens | 29,964 | 28,501 |
| total tokens | 457,977 | 475,734 (+3.9%) |
| mean turns | 9.7 | 8.8 |
| index cost per request | 0 | 1,224 chars |

**Zero wrong-skill loads is the number the two distractors exist to produce.** `qz-deploy` and
`qz-migrate` are plausible, well-written and needed by no case; without a skill that is never the
right answer, "chose correctly" cannot be told from "chose the only option".

The single failure is `skill-deps` run 1, which loaded nothing and hit the turn cap. Not a
discrimination failure.

Trust checks both ways: 0 blocked, 0 tampered, 0 write violations, one model, and **0 runs read the
skill library directly through `/app`** - the bypass that would have invalidated the whole comparison.

### The control run that had to be thrown away, and what it bought

The first control was killed after ONE case-run, because `skill-lint` **passed with skills off**.
The agent had read `tools/qzlint.py` and found the answer in the checker's own source:

```python
BANNER = "# owner:"          # the fix, sitting in the workspace
```

Inspection of the other five found two more: `skill-release` shipped a `VERSION` already reading
`4.11.2-quartz` and a `CHANGES.md` already in the `rel ... ::` form, and `skill-deps` shipped a
`deps.txt` already demonstrating the ` @ ` format. **Three of six measured pattern-matching rather
than whether the document was opened.**

The rule that was broken, now stated: **the workspace may contain the TASK, never the ANSWER.** The
three cases that survived did so because their convention existed only in the skill.

Fixed by starting each from a clean slate - no checker in the workspace, an unsuffixed `4.11.2`, an
empty `deps.txt` - and re-verified in **three** directions rather than two:

```
                 untouched   plausible-guess-without-skill   the skill's answer
skill-lint          fail              fail                        pass
skill-release       fail              fail                        pass
skill-config        fail              fail                        pass
skill-testname      fail              fail                        pass
skill-errors        fail              fail                        pass
skill-deps          fail              fail                        pass
```

**The middle column is the one two-way verification misses**, and it is what one case-run of quota
bought. `skill-lint` then scored 0/3 in the rebuilt control.

### `skill-testname` is the sharpest case in the set

Every model has an overwhelming prior that Python tests begin `test_`. This project's fictional
runner collects `check_`. With skills off the agent writes `def test_add()` in four turns and stops -
fast, confident, wrong. With the skill it writes `check_add`. That is about as clean an adherence
signal as can be constructed, because the only way to produce it is to have read the document.

### The dev regression guard

Skills are pure cost here: an index on every request, a seventh schema, and nothing to load.

| dev suite | M (6 tools) | N (+skills) |
|---|---|---|
| pass | 14/15 | 13/15 |
| total tokens | 444,481 | 448,546 (**+0.9%**) |
| median tokens | 31,582 | 27,053 |
| schema chars | 3,553 | 4,075 |
| index chars | 0 | 1,224 |
| skills loaded | 0 | **0** |
| tampered / violations | 0/0 | 0/0 |

**+0.9% total, while paying 1,224 chars of index on every request.** That is the progressive-disclosure
claim as a measured number rather than a design argument.

**The 13/15 is `add-endpoint` at 1/3, and it is NOT attributed to skills.** That case has scored 2/3
(2026-08-19), 2/3 (K), 3/3 (L), 2/3 (M) and now 1/3 - it is the suite's known-flaky case, its
`failures 3->3/0/3` column shows the agent genuinely failing the task, and **zero skills were loaded
on any dev run**, so the mechanism could not have misled it. At n=3 that cannot be *proved*, so it is
recorded as unresolved rather than dismissed. If it recurs at 1/3 next phase it needs its own cycle.

### Two defects the build caught before any quota

- **The index was silently truncating.** 1,236 chars against a guessed 1,200 cap, cutting off the last
  skill's description. A skill the agent cannot see is indistinguishable in the traces from one it
  chose not to open, so overflow is now **fatal** with an actionable message - the same reasoning that
  makes `MAX_SCHEMA_CHARS` fatal.
- **An unquoted colon silently dropped a skill.** `description: Use when asked to deploy: staging...`
  is not valid YAML; `safe_load` returned something unusable and the document vanished from the index
  with no signal. A human writing these by hand will produce that line. YAML stays the primary parser,
  a fallback scan recovers it, and anything still unusable is reported once at startup.

### Level 3, and the argument for allowing it

Bundled files were supported deliberately, and the reasoning is written down because "a document can
carry executable code into the sandbox" should stop a reviewer.

`load_skill` **reads. It never executes.** A bundled script is run by the agent calling `run_shell` on
it, through `classify()` and the `DANGER` regex like anything else. **That grants nothing `run_shell`
does not already grant** inside a container whose boundary Phase K made kernel-enforced. What Level 3
changes is **provenance** - code arriving in a document rather than from the model - and in Phase N
every skill is written by a human. **Phase O gives the agent that power and must re-open this.**

The escape is tested, not assumed: `../../../etc/passwd`, an absolute path, and a traversal into a
sibling skill are all refused, by the same resolve-and-compare shape `policy._inside_workspace()` uses.

### The fixture library moved before it could mislead

The eight skills first landed at `skills/` in the repository root, where they read as *this project's*
conventions - to a human browsing the tree, and to the agent working in it. They describe a fictional
Quartzite/Ashgrove project. They now live at `eval/fixtures/skills-library/` with a README saying so,
and the harness points `AGENT_SKILLS_DIR` there for every scored run, making the library a recorded
measurement condition rather than something ambient. The interactive agent sees `skills/` at the root,
which is empty - so it pays **no index cost at all** rather than 412 tokens a turn for fictions.

### `registry.py` earned its keep

Phase M created it for the merge and the budget. Phase N added a third tool source and touched it
once, by one line. That is what a registry is for.

### Tests

**247 offline**, up from 225 - no API key, no network, read-only root. 22 are new, including all four
path-escape attempts and the parser defect above.

### Phase N scoreboard

| | result |
|---|---|
| load rate | **17/18 correct, 0 wrong** |
| delta in success | **0/18 -> 17/18** |
| disclosure claim | **+0.9% on work that loads nothing** |
| dev regression | 13/15; the drop is the known-flaky case, unresolved not dismissed |
| authoring | **not built** - Phase O, with its own measurement |

**Kept.**

---

## Phase O — Authoring: NOT SHIPPED. The agent will not write a skill.

**Result: `learn` called ZERO times in 15 valid sessions**, with the tool exposed, an explicit
instruction in `SOUL.md` telling it to, three different turn budgets, and a task small enough to
finish with turns to spare. The kill switch stays off and the feature does not ship.

### What was actually eliminated

| hypothesis | how it was ruled out |
|---|---|
| runs out of turns | finished `done` at 7-9 of 12, with 3-5 turns spare |
| runs out of budget | 25k of a 100k budget used |
| task too complex to converge | reduced to ONE file and ONE rule; still nothing |
| tool not exposed | `schema_chars` 4,853 = 8 tools, `authoring: true` on every row |
| prompt does not ask | `SOUL.md` amended to say it explicitly; three variants |

What is left is the finding: **the model does not treat "record this for later" as part of the job.**
It completes the work and stops, which is exactly what it was told to do. Consolidation is not
something it elects to do.

The clearest single trace is the tiny probe's second session:

```
session 1:  read_file  CONVENTIONS.md          <- brief present, read it, wrote the file, DONE at 7 turns
session 2:  read_file  CONVENTIONS.md   ERR    <- reached for it; the workspace was reset
```

It remembers that a conventions file existed. It does not remember what the file said, and it has no
skill to fall back on - which is precisely the gap authoring was built to close.

### Four attempts were discarded before the benchmark was sound

Recorded because they are the phase's real cost, and because three of the four are rig defects of a
kind this project keeps producing as the benchmarks get more elaborate.

1. **Inherited Phase N's skill library.** The harness pointed every scored run at
   `eval/fixtures/skills-library`, and the authoring cases reused the same four conventions - so the
   agent loaded the human-written `qz-release` and passed on knowledge the case existed to withhold.
   Fixed: `AGENT_SKILLS_DIR` is per-case, and the authoring split starts from `skills-empty`.
2. **`inner()` ran `case["setup"]` and consulted `setups` only from session 2 onward.** So session 1
   got the WRONG fixture and **no session ever received `CONVENTIONS.md`**. Every run before this was
   measuring an agent with nothing to learn. The offline verifier missed it because it tested
   `setups[0]` directly while the harness used a different code path for that same session -
   **verifying the data is not verifying the path that consumes it.**
3. **Operator error, and the worst of the four.** With a run waiting on
   `await_exclusive_workspace()`, the blocking container was force-removed by hand to unblock it.
   That guard exists because case-runs share one `/workspace`; defeating it is exactly how one run's
   `reset.sh` lands in another's directory. This project had already invalidated three runs that way.
   **When the log says "waiting for N agent container(s)", let it wait.**
4. **No headroom.** With `max_turns: 12`, session 1 spent 11-12 turns on the task alone. Raising it
   to 20 only moved the wall from the turn cap to the token budget (`compact` at 18-19 turns, 60k a
   run) - the agent expands task work to fill whatever it is given. The tiny probe removed this
   variable entirely.

### The tiny probe, which is what settled it

One case, three runs, two sessions, and session 1 is a single file write against a single rule
(`# owner: unassigned`). Verified three ways first, and the brief-gap check confirmed present in
session 1 and gone in session 2.

```
run 0   done   9/12 turns   26,929 tok   authored=[]
run 1   done   8/12 turns   25,449 tok   authored=[]
run 2   done                             authored=[]
```

**Finishing cleanly with headroom and still not authoring is what makes this a finding rather than a
budget problem.**

### What this says about the design, and it matches a deferral already on record

**Authoring-on-completion is the wrong hook.** It depends on the agent electing to reflect after the
work is done, and it does not. The ROADMAP already deferred "self-improvement of skills during use"
as a separate capability; that deferral now looks like the load-bearing decision rather than a
scoping convenience.

Two designs do not depend on the agent's choice:

- **Amend during use** - capture at the moment the knowledge is acquired. Needs a model call inside
  the loop and its own measurement.
- **Deterministic extraction at `finish`** - and this is the cheap one. The convention was READ FROM
  A FILE, so its content is already in the trace. `finish` can write the skill with **no model call,
  no fourth model-calling node, and no reliance on the agent's judgement.** The benchmark, fixtures
  and instrumentation all exist and are now verified, so this is one function plus a call.

Try the free version before the expensive one.

### What is kept, and what is reverted

**Kept:**
- **The `setups[0]` fix in `inner()`** - a real harness defect that would silently break any future
  per-session case, quite apart from Phase O.
- **Per-case `AGENT_SKILLS_DIR`**, and `eval/fixtures/skills-empty` - a scored split can now declare
  the library it was measured against, rather than inheriting another phase's.
- **`learn` in `agent/skills.py`**, text-only, behind `AGENT_SKILL_AUTHORING=off`. Enforcement is an
  ABSENT parameter rather than a rule: no path or file argument exists, and the slug alphabet cannot
  express a separator, so `../../etc/passwd` becomes a directory called `etc-passwd`.
- **The `authoring` and `authoring-tiny` splits**, three-way verified, with the brief-gap check.
- **256 offline tests**, up from 247.

**Reverted:**
- **`prompts/SOUL.md`.** The widening was measured and did not work; it touches every split and was
  never regression-tested. Keeping an unearned prompt change would be exactly the drift the Iron Law
  exists to prevent.
- **`max_turns: 20`** on the authoring cases, back to 12. It was a probe, not a finding.

### Phase O scoreboard

| | result |
|---|---|
| authoring rate | **0 in 15 valid sessions** |
| reuse rate | not reached - nothing was ever authored |
| delta in success or cost | not reached |
| **verdict** | **NOT SHIPPED.** `AGENT_SKILL_AUTHORING=off` is the default |

**The exit criterion said "without all three numbers this phase does not ship". It has one, and that
one is zero.**

---

## Phase O-redux — Deterministic extraction: the rule fires, the agent never reads it

**Extraction rate 3/3. Load rate 0/3. Pass 0/3 against a 0/3 control.** The mechanism works and
delivers nothing, because the failure moved one step downstream.

### What was built

`finish` now writes a skill from any document the agent **read and never edited**, with no model call
and no decision by the agent — everything needed is already in `state["messages"]`, which is the same
property that lets Phase M write episodes there. `AGENT_SKILL_EXTRACTION=off` by default.

The rule is a deterministic stand-in for the judgement `learn` asked the model for and Phase O
measured it declining 15 times out of 15. A file read and never written to is a reference; anything
the agent wrote is its own output.

### The isolation run, which is the only number that means anything

The first tiny-split run scored **2/3** and it was not extraction that earned it:

| arm | pass | extracted | loaded | memory_chars |
|---|---|---|---|---|
| extraction + memory | 2/3 | 3/3 | 1/3 | up to 630 |
| **control: both off** | **0/3** | 0/3 | 0/3 | 0 |
| **extraction only, memory off** | **0/3** | **3/3** | **0/3** | 0 |

Of the two passes in the first run, one came from `memory_chars: 630` — Phase M's episode carried the
convention and the agent never opened the skill at all. **What that run actually compared was
"extraction + memory" against "memory", and memory won on its own.**

Removing memory from both arms isolates it, and the answer is flat: extraction fires every time and
changes nothing.

### Where the wall actually is, and it is not extraction

Phase O found the agent will not WRITE a skill. This finds it will not READ one either, unless the
task advertises its domain:

| | the task | load rate |
|---|---|---|
| Phase N | *"run this project's style checker"* | **17/18** |
| here | *"create c.txt containing gamma"* | **0/3** |

A task that looks self-contained gives the agent no reason to suspect a convention exists, so it
never looks. The index was present on every request (378 chars) and said, in as many words, *"Check
it BEFORE creating or changing files."* It did not act on that.

**So `load_skill`'s discoverability is the binding constraint, not extraction.** Extraction is a
working component feeding a retrieval path that only fires when the task hints at it.

### Two defects the runs caught in code written minutes earlier

Both had reproducing tests added before the fix:

- **The description described the wrong thing.** Derived from the session's GOAL, it read *"Use when
  working on tasks like: create b.txt containing beta"* — one specific task. A later session asking
  for `c.txt` matched nothing. It now derives from the DOCUMENT's own first heading and names a class
  of work: *"Use when the one rule applies... Check it BEFORE creating or changing files."*
- **`read_file`'s line-number gutter survived into the skill body**, so the document read
  `     1\t# The one rule`. Numbers are how the tool shows a file to the model, not part of what the
  file says.

### Kept, and why, given a null result

- `extract()` and `read_but_not_edited()` in `agent/skills.py`, **default off**. The next attempt
  reuses both, and the component is not what failed.
- `skills_extracted` and `extraction` on every row — the instrumentation is what made the memory
  confound visible, and it would have hidden it otherwise.
- `agent/skills.py` stays a leaf module: the plan had it import `_outcomes` from `graph.py`, which
  would drag in langgraph and close an import cycle (graph already imports skills). Six lines inlined
  instead.

### The next test, and it is the same move that made extraction work

Take the decision away from the model: **auto-inject the top-matching skill's body** at session start
rather than only its index. With one or two skills the cost is bounded and it is one function in
`act`. If it passes, the loop closes end to end with no model judgement anywhere in it. If it still
fails, the agent ignores injected knowledge — a far larger finding than anything about skills.

**269 offline tests**, up from 264.

---

## Plan mode — built, enforced, instrumented; the plan is never written

**Nine scored runs, three cycles, one case. `adopt` fell back to the goal copied verbatim in
every single run.** The phase runs, the read-only gate holds, the cost is real — and FR-101, the
requirement the whole phase was justified by, is **not satisfied**. Default `AGENT_PLAN=off`.

### Why it was built at all, stated before the runs

Not by the pass rate. §9's trigger is *"`stuck` at max_turns, no repeats → no strategy → plan
node"*, and measured across all 676 rows on the current model it looks stronger than it is:

| split | runs | stuck at cap | | reading |
|---|---|---|---|---|
| authoring | 87 | 42 | 48% | already diagnosed: it will not *write* skills |
| skills | 71 | 32 | 45% | already diagnosed: it will not *read* them |
| dev | 133 | 32 | 24% | **18 of those are `add-endpoint` alone** |
| real | 65 | 6 | 9% | the split with headroom — `compact` dominated |
| web, multibug, pilot | 72 | 0 | 0% | |

The two largest contributors had their failures attributed elsewhere already, and counting them
again here would be double-counting. `replan` — §3's other route into this node — fired **once
in 712 rows**. So the justification was FR-101, FR-105, FR-702, UR-02 and UR-05, and the
pass-rate prediction was narrowed in advance to one case: `add-endpoint`, 18 of dev's 32
stuck-at-cap runs, sitting at 1/3.

### The three cycles

| cycle | change | pass | plan written? | plan_turns |
|---|---|---|---|---|
| 1 | the phase, as designed | 1/3 | **no** — cap exited straight to `adopt` | 4/4/4 |
| 2 | reaching the cap asks for the plan instead of exiting | 3/3 | **no** — model called a tool anyway | 5/5/5 |
| 3 | the final planning turn is sent with **no tool schemas** | 2/3 | **no** — model called a tool anyway | 5/5/5 |

**1/3 → 3/3 → 2/3 is noise on a case that was already 1/3, and this project has been burned by
exactly this shape before** — a pilot case scored 1/3, was written up as a capability limit, and
scored 3/3 unchanged in the full run. Nothing here is attributable, and it would not be even if
the numbers had held: the mechanism never fired, and a pass rate is not evidence for a mechanism
that did not run. That is Phase O-redux's rule, applied to itself.

### The cause, proven directly rather than inferred

Cycle 3 removed the tool schemas entirely, so the model *could not* see a tool to call. It
called one anyway. One direct request settled why — `tools` absent from the payload, a short
history containing two prior tool calls:

```
finish_reason: tool_calls
tool_calls:    ['read_file']
text:          ''
```

**On this provider, a history full of tool calls keeps producing tool calls whether or not any
tool is offered.** Neither an instruction nor an absence stops it. So the planning phase never
reached a text-only reply, `reflect`'s hard stop fired on a tool result, and `adopt` had nothing
to parse.

### What that says about the design decision, which was mine and was wrong

§3 draws `PLAN` as a node with its own model call. It was built as a **phase** of the existing
loop instead, justified under CE-04 — two nodes that never branch apart are one node — because
planning seemed to differ from working only in the prompt, the gate and reflect's exit.

**They do branch apart, in the one way that mattered: the message list.** A phase inherits the
tool-call history, and that history is exactly what prevents the plan from being written. A plan
node with a *fresh, short* message list — the goal plus a digest of what was read — would not
have this problem, and that is what §3 was drawing. CE-04 did not apply, and the measurement is
what showed it.

### What is kept, and why, given the null result

Default **off**, so nothing here is claimed. Kept because the next attempt reuses all of it and
none of it is what failed:

- `phase` / `plan` / `cursor` / `plan_turns` on the state, and §9 step 2(b)'s cursor check
  restored — it said to do so "only when the plan node is added".
- **The read-only gate, which is the part that demonstrably works.** `classify(..., planning=True)`
  refused every write across nine runs; `plan_denied` recorded `pytest -q` on all nine, the
  predicted failure written down before the first run. An allowlist rather than "risk == read"
  because there is no directory-listing tool, so refusing `run_shell` outright would leave the
  planner guessing at the tree.
- `PLAN_MAX_TURNS`, separate from `MAX_TURNS`. With a 12-turn cap, research on a shared counter
  would have starved the very case this targeted.
- `plan_steps` / `plan_turns` / `plan_denied` on every row — the instrumentation is the only
  reason the fallback was visible at all. Without `plan_steps` the 3/3 in cycle 2 would have
  been recorded as planning working.
- `reason` on every tool trace entry. The trace had always said a call was denied and never on
  what grounds.

### Costs, stated because they are real

- **NFR-402 breached**: median 60,229 / 64,997 / 66,056 tokens against the 60,000 ceiling. Planning
  spends a model call plus four to five reads on every run.
- `agent/provider.py` now omits `tools` rather than sending `[]` — several OpenAI-compatible
  endpoints reject an empty array. Kept regardless of this phase's outcome; it is a correctness
  fix that cost nothing.

### The next test

A real plan **node** with its own message list: the goal, a digest of what the research turns
read, and no tool-call history. If the plan still is not written with a clean context, the
finding is about the model rather than the design — and that is a much larger result than
anything about planning.

**346 offline tests**, up from 343.

---

## Stage 1 — six audit closures, no third-party code, no quota

A requirement-by-requirement audit against CONTEXT.md found **21 of 35 must-haves
satisfied**, 11 unmet, 3 partial, and one Definition-of-Done item false as written. Hermes was
copied to `hermes_copy/` to fix them. **Four of the six turned out to have nothing to copy** -
Hermes hand-writes all 84 of its tool schemas, its code tool is a subprocess runner with no
final-expression value, it has no directory-listing tool, and its seven `_redact_*` helpers are
each tool-specific with no shared utility. So Stage 1 is written here, and costs nothing.

| requirement | was | now |
|---|---|---|
| **NFR-601** adding a tool touches one file | **false as written** - `TOOLS` in tools.py, `RISK` in policy.py | `risk` declared beside the schema; `policy.sync()` reads it |
| **FR-201** list directories | no tool; `run_shell` only | `read_file` on a directory returns the listing |
| **FR-203** run Python, return the final expression | no tool at all | `run_python`, exposed and gated at `write` |
| **NFR-203** secrets never in context | env indirection only, **no redaction code existed** | `redact()` inside `shrink()`, covering the spill too |
| **NFR-304** caps on turns, tokens **and wall-clock** | two of three | `spent_seconds` accumulated, `MAX_SECONDS` enforced |
| **FR-804** delta against the previous run | runs versioned, no delta | `delta()`, per case and per token |

### The one that was a real defect rather than a gap

**NFR-601 was in the Definition of Done and had never been true.** `TOOLS` carried the function
and the schema; `RISK` was a literal in `policy.py`. Every built-in since v1 touched both files,
and the project had been reading "one file" as "one file plus the risk map". A tool now declares
its risk beside its schema - the shape `memory.tools()` and `skills.tools()` already used -
and `policy.risk_of()` falls back to `tools.TOOLS`, so a tool added after import is classifiable
without anyone remembering to call `sync()`. **DoD goes 8/9 -> 9/9, honestly this time.**

### Two things the runs taught, applied here

- **`read_file` on a directory now ANSWERS instead of advising.** Three versions of this, each
  paid for: a bare `[Errno 21]` was the only failure in the 14/15 baseline (3 of 12 turns burned
  retrying); naming `ls` fixed that; and the planning traces then showed the remaining cost -
  read_file, error, `ls -la` on the same path, two turns for one answer. A dedicated list tool
  would cost **~582 chars of schema on every request** against a 6,000 cap, to answer a question
  `read_file` is already being asked.
- **A tool the model cannot see is a function, not a capability.** `run_python` and its five
  tests existed and passed for a while with no `TOOLS` entry - the suite was green and the model
  had never been offered it. Caught by a live toolset check, not by the suite, and there is now a
  test asserting the schema is registered.

### Cost, measured

Schema budget **3,518 / 6,000** chars with memory and skills active (≈4,680 with MCP's `fetch`),
so `run_python` fits with room to spare. **368 offline tests**, up from 346. Zero model quota.

### What Stage 1 did NOT close, and why

**FR-104** - "terminate on exactly one of done, stuck, budget exhausted, turn cap reached" -
stays open. Runs still end `compact` and `replan`, neither of which is on that list, and there is
no `budget` verdict at all. `compact` cannot stop being terminal until the compaction node exists
(Stage 3), and mapping it to "budget exhausted" would be a lie: it fires at 60% of budget, not at
exhaustion. **NFR-304 was deliberately built to avoid making this worse** - running out of
wall-clock terminates as `stuck` rather than as a fifth verdict.
