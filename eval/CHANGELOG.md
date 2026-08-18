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
