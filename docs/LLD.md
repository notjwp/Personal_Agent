# Low-Level Design

**Status:** derived document. `CONTEXT.md` is binding. `ARCHITECTURE.md` records *why*; `HLD.md`
records *what*; this document records *how* — signatures, data shapes, algorithms, file formats, and
the test matrix.

**Version-dependent items are marked ⚠. Verify them against the installed release before building
on them; do not assume.**

---

## 1. Module: `agent/config.py`

Constants only. No functions, no I/O beyond reading environment variables.

```python
WORKSPACE       : Path   # os.environ["AGENT_WORKSPACE"], default "/workspace", resolved
ARTIFACTS       : Path   # WORKSPACE / ".agent" / "artifacts"
STATE_DB        : Path   # AGENT_HOME / "state.db"   (outside WORKSPACE — survives reset.sh)

MODEL           : str    = "claude-opus-5"
EFFORT          : str    = "medium"        # low | medium | high | xhigh | max
MAX_TOKENS      : int    = 16_000          # caps thinking + text together on this model

MAX_TURNS       : int    = 12
BUDGET_TOKENS   : int    = 200_000
COMPACT_AT      : float  = 0.60            # reflect check (a)

MAX_RESULT_CHARS: int    = 6_000           # NFR-104 ceiling, ~2000 tokens at 3 chars/token
TOOL_CAPS       : dict   = {"read_file": 6_000, "write_file": 400, "run_shell": 6_000}
HEAD_LINES      : int    = 30
TAIL_LINES      : int    = 20
```

**Why the cap is in characters:** NFR-104 is stated in tokens, but a token-accurate check needs an
API call, and NFR-602 requires the unit tests to run without a key. Characters at a conservative
3 chars/token give a bound that is testable offline; the harness performs the exact token check
separately, where a key is present.

**Why `ARTIFACTS` is inside the workspace and `STATE_DB` is not:** §4.3 requires the model to read
spilled artifacts, and FR-302 rejects any path outside the workspace — so artifacts must be inside.
Checkpoints must survive `reset.sh`, which wipes the workspace — so `state.db` must be outside.

---

## 2. Module: `agent/policy.py`

### 2.1 Signature

```python
def classify(name: str, args: dict, autonomous: bool) -> tuple[str, str]
    # returns (verdict, reason); verdict ∈ {"auto", "confirm", "deny"}
```

**Contract: pure.** Same inputs → same outputs. No logging, no counters, no writes, no clock reads.
Enforced by a test that runs `classify` twice and asserts an unchanged filesystem.

### 2.2 Algorithm

```text
1. For each key in PATH_ARGS = ("path", "file", "cwd"):
       if key in args and not _inside_workspace(args[key]):
           return ("deny", "path escapes workspace: …")        # FR-302, unconditional

2. risk := RISK.get(name)
       if risk is None: return ("deny", "unknown tool: …")

3. if name == "run_shell" and DANGER.search(args["command"]):
       risk := "destructive"                                    # escalation, not a bypass

4. verdict := VERDICT_BY_RISK[risk]

5. if autonomous and verdict == "confirm":
       return ("deny", "… queued for review")                   # FR-304
   return (verdict, …)
```

### 2.3 Tables

```python
RISK            = {"read_file": "read", "write_file": "write", "run_shell": "write"}
VERDICT_BY_RISK = {"read": "auto", "write": "auto", "destructive": "confirm"}
```

`run_shell` is declared `write` and escalated by the danger pattern in step 3. **This is why `RISK`
is the single live path** — a name special-case that returned before consulting `RISK` would make
the declaration dead, and any future tool marked `destructive` would then be silently auto-denied
during eval.

### 2.4 Danger pattern

Matches: `rm -rf` and flag variants · `git push --force` · `git reset --hard` · `sudo` · `mkfs` ·
`dd if=` · `shutdown`/`reboot`/`halt` · `chmod -R 777` · `curl … | sh`.

Test both directions: each pattern classifies `destructive`, and benign commands (`pytest -q`,
`ls`, `git status`) classify `auto`.

### 2.5 Path resolution

```python
def _inside_workspace(value: str) -> bool:
    candidate = Path(value)
    resolved  = (candidate if candidate.is_absolute() else WORKSPACE / candidate).resolve()
    return resolved == WORKSPACE or WORKSPACE in resolved.parents
```

`.resolve()` follows symlinks, so a symlink *inside* the workspace pointing outside resolves outside
and is rejected. `OSError` / `ValueError` / `RuntimeError` (loops, invalid names) → `False`, fail
closed.

---

## 3. Module: `agent/context.py`

### 3.1 Signature

```python
def shrink(tool: str, text: str) -> str
```

Returns a **plain string, not a tuple**. The spill path belongs inside the returned text — that is
the only place it was ever useful.

### 3.2 Algorithm

```text
cap := min(TOOL_CAPS.get(tool, MAX_RESULT_CHARS), MAX_RESULT_CHARS)

if len(text) <= cap:
    return text                                    # no artifact written

mkdir ARTIFACTS (at call time — CE-05)
artifact := ARTIFACTS / f"{sha256(text)[:16]}.txt"
write full text to artifact

if line_count > HEAD_LINES + TAIL_LINES:
    head, tail := first 30 lines, last 20 lines
    elided     := "[N lines elided, M chars total]"
else:                                              # one enormous line
    head, tail := text[:cap//2], text[-cap//2:]
    elided     := "[N chars elided]"

return head + elided + tail + artifact path + INSPECTION INSTRUCTIONS
```

**Content-addressed naming** means a repeated identical output reuses one artifact instead of
accumulating duplicates.

### 3.3 Required output elements

All five must be present; each is asserted separately in tests.

1. Head lines · 2. Elision marker · 3. Tail lines · 4. Absolute artifact path ·
5. **Inspection instructions** naming both `read_file(path=…, offset=N, limit=M)` and
   `run_shell(command='grep -n PATTERN …')`.

Element 5 is not decoration. A path with no instruction is ignored by the model in practice.

---

## 4. Module: `agent/tools.py`

### 4.1 Signatures

```python
def read_file(path: str, offset: int = 0, limit: int = 500) -> str
def write_file(path: str, content: str) -> str
def run_shell(command: str, timeout: int = 120) -> str
```

- `run_shell` executes with `cwd=WORKSPACE` and returns exit code, stdout and stderr **separately
  labelled** (FR-202).
- Tools **raise** on failure. They never return an error string — `execute` owns the
  exception→observation conversion (FR-208).
- Tools do **not** re-check paths. The gate checks declared path arguments; the container's single
  writable mount bounds everything else. Two mechanisms guarding one risk is a CE-02 violation.

### 4.2 Registry shape

```python
TOOLS = {
    "read_file":  {"fn": read_file,  "schema": {...}},
    "write_file": {"fn": write_file, "schema": {...}},
    "run_shell":  {"fn": run_shell,  "schema": {...}},
}
SCHEMAS = [t["schema"] for t in TOOLS.values()]
```

Schemas are hand-written: the `@tool` decorator costs ~25 lines plus ~5/tool against ~8/tool
hand-written, so break-even is five tools and v1 has three. `SCHEMAS` order must stay deterministic —
tools render first in the prompt, so a reordering invalidates the entire prompt cache.

Adding a tool touches this file only (NFR-601).

---

## 5. Module: `agent/graph.py`

### 5.1 State

```python
class AgentState(TypedDict):
    messages: list[dict]      # default overwrite; nodes return the FULL list
    turns: int
    max_turns: int
    spent_tokens: int
    budget_tokens: int
    failures: int             # CONSECUTIVE failed turns, reset to 0 on a clean turn
    verdict: str | None
    approved: list[dict]
    denied: list[dict]
```

Nine fields exactly. No reducers, no `Annotated`, no `operator` import. Because nodes return the full
`messages` list, compaction later becomes an ordinary return rather than a custom merge.

**Config-borne, not state-borne:** `thread_id`, `autonomous`, `on_text` (the CLI's streaming
callback). These are run-level configuration; putting them in state would break §13's shape and
CE-03.

### 5.2 Node: `act`

The model adapter (NFR-702) **is this function** — one caller, one implementation, so CE-01 forbids a
separate `agent/llm.py`.

```python
client = anthropic.Anthropic()                       # CE-05: built here, not at import
system  = read prompts/SOUL.md                       # CE-05: read here, not at import

with client.messages.stream(
    model=MODEL, max_tokens=MAX_TOKENS,
    system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
    thinking={"type": "adaptive"},
    output_config={"effort": EFFORT},
    tools=SCHEMAS,
    messages=state["messages"],
) as stream:
    if on_text: forward deltas                       # CLI streams; harness passes None
    response = stream.get_final_message()

content = [block.model_dump() for block in response.content]
returns  messages + [{"role": "assistant", "content": content}]
         spent_tokens + usage.input_tokens + usage.output_tokens
```

**Model contract for `claude-opus-5` — all of these are hard constraints, not preferences:**

| Constraint | Consequence |
|---|---|
| Thinking is **on by default** | `max_tokens` caps thinking + text together; size it accordingly |
| `budget_tokens` returns 400 | Control depth with `output_config.effort`, never a token budget |
| `temperature` / `top_p` / `top_k` return 400 | Steer by prompting. There is no sampling knob and therefore no seed |
| Assistant-turn prefill returns 400 | Never prefill; use structured outputs if a shape is needed |
| `thinking.display` defaults to `"omitted"` | Thinking blocks arrive with empty text but **must be echoed back unchanged**, signature included |
| SDK `max_retries=2` = 3 attempts, backoff | Exactly NFR-303. Do not add a custom retry loop |
| Render order is `tools → system → messages` | A `cache_control` breakpoint on the system block caches tools *and* system |

`block.model_dump()` preserves the thinking signature and yields JSON-serialisable dicts, which is
what makes the state checkpointable. ⚠ Verify the round-trip on the first live call.

### 5.3 Node: `gate`

```python
for call in tool_calls(last assistant message):
    verdict, reason = classify(call.name, call.input, autonomous)
    auto    -> approved.append(call)
    deny    -> denied.append(call + reason)
    confirm -> decision = interrupt({call, reason})       # suspends here
               allow -> approved / else denied
returns {"approved": [...], "denied": [...]}
```

**NO SIDE EFFECTS.** This node re-executes from its first line when the run resumes after approval,
rebuilding `approved`/`denied` from scratch. No logging, no counters, no database writes. This is
CE-07 and FR-305, and it is why `gate` and `execute` are separate nodes: merged, every
already-executed tool would fire a second time on every resume.

⚠ With more than one `confirm` in a single turn, LangGraph matches resume values positionally by
interrupt index. Verify against the installed version before relying on multi-approval turns.

### 5.4 Node: `execute` (absorbs `observe`, CE-04)

```python
failed := 0
for call in approved:
    t0 := monotonic()
    try:    raw, is_error := TOOLS[call.name].fn(**call.input), False
    except: raw, is_error := f"{type}: {exc}", True          # FR-208
    if is_error: failed += 1
    body := shrink(call.name, raw)                            # FR-401/402
    results.append(tool_result(call.id, body, is_error))
    log_call(call, "auto", monotonic()-t0, raw, body)         # FR-805 / NFR-501

for call in denied:
    results.append(tool_result(call.id,
        f"Denied by policy: {reason}. Find another approach.", is_error=True))
    log_call(call, "deny", 0.0, "", "")                        # a denial is a tool call
    failed += 1

returns messages + [{"role": "user", "content": results}]      # ALL results, ONE message
        turns + 1
        failures = 0 if failed == 0 else failures + 1
```

**All `tool_result` blocks go in a single user message.** Splitting them across messages trains the
model to stop making parallel calls.

**`failures` is a plain int with overwrite semantics.** As an accumulating list it would latch at
`>= 3` for the rest of the run, because returning `[]` appends an empty list rather than clearing it.

### 5.5 Node: `reflect`

Deterministic only — no model call. Checks in fixed order, first match wins:

| # | Condition | Verdict |
|---|---|---|
| a | `spent_tokens > COMPACT_AT * budget_tokens` | `compact` |
| b | `turns >= max_turns` | `stuck` |
| c | Last 3 assistant turns have identical call signatures | `stuck` |
| d | `failures >= 3` | `replan` |
| e | Last message role is `assistant` | `done` if any tool call was ever made, else `continue` |
| f | otherwise | `continue` |

**Check (e) — the termination guard.** A cursor-based check with no plan node evaluates `1 >= 0` and
returns `done` on the first text-only reply, including *"Let me look at the test file first."*
Restore the cursor check only when the plan node lands.

```python
def _made_a_call(messages) -> bool:
    return any(block["type"] == "tool_use"
               for m in messages if m["role"] == "assistant" and isinstance(m["content"], list)
               for block in m["content"])
```

**Call signature** for check (c): `f"{name}:{sha256(json.dumps(input, sort_keys=True))[:12]}"`.
`sort_keys=True` is required — unsorted serialisation makes identical calls look distinct.

### 5.6 Node: `finish`

Terminal. Records verdict, turns and tokens to the trace (NFR-502). §4.1 also has `finish` write
durable memory; that half arrives with the memory layer (L4).

### 5.7 Wiring

```python
START      -> act                                   # not "plan": there is no plan node at v1
act        -> gate (tool_use present) | reflect (text only)
gate       -> execute                               # CE-07: never merged
execute    -> reflect
reflect    -> act ("continue") | finish (everything else)
finish     -> END
```

Compiled with a SQLite checkpointer over `STATE_DB`. ⚠ `SqliteSaver.from_conn_string` is a context
manager in recent releases, and `interrupt`'s import path has moved between versions — pin both and
verify construction.

---

## 6. Data formats

### 6.1 Fixture case (`eval/tasks.jsonl`, one object per line)

```json
{"id":"fix-import","goal":"Tests in tests/ fail on an import error. Fix it.",
 "setup":"scripts/reset.sh fix-import","check":"cd /workspace && pytest -q",
 "split":"dev","max_turns":12,"budget":200000}
```

`split` ∈ `{"dev", "heldout"}`. Five dev cases, ten held-out.

### 6.2 Per-call trace record (NFR-501)

```json
{"tool":"run_shell","argument_hash":"a1b2c3d4e5f6","verdict":"auto",
 "duration_ms":412,"input_bytes":38,"output_bytes":91204,
 "spill_path":"/workspace/.agent/artifacts/9f2c….txt","is_error":false}
```

### 6.3 Per-case trace (`eval/runs/<ts>/<case-id>.json`)

Full final message list plus the per-call records above plus the terminal record. §9 Step 4 is
unactionable without this, which is why tracing is scoped into Step 2 rather than deferred.

### 6.4 Run summary (`eval/runs/<ts>/summary.jsonl`)

One row per case per run: `id`, `run_index`, `pass`, `turns`, `tool_calls`, `tokens`, `seconds`,
`verdict`.

**`run_index`, not `seed`.** On `claude-opus-5` there is no temperature or seed parameter, so the
three runs §9 requires are repeat invocations and the variance is model nondeterminism. Naming the
field `seed` would imply a knob that does not exist.

---

## 7. `scripts/reset.sh`

```bash
set -euo pipefail
: "${AGENT_WORKSPACE:?must be set}"                  # no second default — config.py owns it
find "$AGENT_WORKSPACE" -mindepth 1 -maxdepth 1 -exec rm -rf {} +   # contents, not the mount
cp -a "eval/fixtures/${case_id}/." "$AGENT_WORKSPACE/"
```

- **`-mindepth 1`** wipes contents while preserving the bind-mount point itself.
- **`find … -exec rm -rf`** removes dotfiles and untracked files. `git checkout` alone would leave
  agent-created files behind — the trap §9 names explicitly.
- Fails loudly when `AGENT_WORKSPACE` is unset, so the default string exists only in `config.py`.

---

## 8. Test matrix

All tests run without an API key and without network access (NFR-602).

| File | Covers | Key cases |
|---|---|---|
| `test_policy.py` | `classify()` | Relative escape · absolute escape · symlink escape · each danger pattern · benign shell → `auto` · destructive + autonomous → `deny` · destructive + interactive → `confirm` · unknown tool → `deny` · **purity** (twice → identical, filesystem unchanged) |
| `test_context.py` | `shrink()` | Under cap → verbatim, no artifact · over cap → all five required elements · artifact holds the full text · single giant line still bounded · result ≤ `MAX_RESULT_CHARS` |
| `test_reflect.py` | verdict logic | Each of (a)–(f) in isolation · precedence: (a) beats (b) beats (c) beats (d) · **text-only reply before any tool call → `continue`, not `done`** · text-only after a tool call → `done` |
| `test_nodes.py` | `gate`, `execute`, `finish`, tools | Mixed verdicts in one turn · **gate has no side effects across two calls** · tool exception → observation, not crash · `failures` resets to 0 on a clean turn · denied call yields a synthetic error observation · `read_file` offset/limit · `run_shell` separates stdout/stderr and honours timeout |

`test_nodes.py` is a stated deviation from §12's three-file `tests/` list: §10 requires unit tests
for *every* deterministic node, and none of the three named files covers `gate`, `execute` or
`finish`.

---

## 9. Error handling

| Failure | Handled where | Behaviour |
|---|---|---|
| Tool raises | `execute` | Caught, converted to a `tool_result` with `is_error: true`; never propagates out of the node |
| Path escapes workspace | `classify` | `deny` + reason; surfaces to the model as an observation |
| Destructive command, autonomous | `classify` | `deny` + queued for review; the agent must find another route |
| Model 429 / 5xx | SDK | 3 attempts, exponential backoff (NFR-303) |
| Model refusal | `act` | Check `stop_reason` before reading `content`; a refusal has no text block to index |
| Shell timeout | `run_shell` | Raises → becomes an observation with the elapsed time |
| Budget exceeded | `reflect` (a) | Terminal at v1; becomes `compact` when L1 lands |
| Process killed | Checkpointer | At most one node lost; resume with the same `thread_id` |

---

## 10. Build order

Implementation follows §9 strictly — it is the only build order.

| Phase | Deliverable | Exit criterion |
|---|---|---|
| A | Fixtures, `reset.sh`, harness, null agent | Harness prints `pass 0/5`; every setup exits 0, every check exits non-zero; a hand-fixed case flips to pass |
| B | `policy` → `context` → `tools` → `graph` → tracing | ≥1 dev case passes; traces readable for all five; an injected tool exception appears as an observation |
| C | CLI | Interactive run with a working approval prompt |
| D | Baseline at 3 runs per case | Committed baseline row: pass rate, variance, median turns, median tokens |
| E | SIGKILL/resume, NFR-201 and NFR-402 assertions | Resume completes with no duplicated side effects |
| F | Tuning cycles | One change per cycle, logged in `eval/CHANGELOG.md`, kept or reverted |
| G | Ten held-out cases | Dev ≥4/5 across 3 runs; held-out scored once and recorded |

The null agent in Phase A exists so that a `0/5` in Phase D is unambiguous between "the agent failed"
and "`reset.sh` is broken". That ambiguity, once introduced, is permanent.
