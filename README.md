# personal-agent

A single-user **personal AI agent**: you give it a goal in plain English, it plans, calls tools, and
works until it reaches a terminal verdict — `done`, `stuck`, or out of budget — then stops and tells
you which.

Three things separate it from chat with function calling, and each is a layer that is not allowed to
collapse into the others:

- **Every tool call is judged before it happens.** A pure `classify()` labels each call
  `auto` / `confirm` / `deny` *before* any side effect. Destructive commands pause for approval
  interactively and are refused outright when running unattended.
- **Nothing reaches the model unfiltered.** Tool output is capped, spilled to disk when it overflows,
  and returned with instructions for reading the rest — otherwise one `pytest` run floods the context.
- **It survives being killed.** State is checkpointed after every step. A `docker kill` mid-task
  resumes having lost at most one step, with no duplicated side effects. Verified, not asserted.

**Only one component ever talks to the model.** The gate, the context manager, the verdict logic and
the harness are ordinary deterministic code — all 174 tests run with no API key and no network.

### Why the evaluation is full of code repair

Because it is the only work with an objective scorer. A test suite exits 0 or it does not; "summarise
my email well" has no exit code. The coding cases are a **capability probe, not the product** — an
agent that cannot fix a real bug in an unfamiliar repository is unlikely to handle your other work
either, and without a scorer you could never tell.

Success is measured, not asserted. Every change goes through one cycle: one change, three runs per
case, keep it or revert it, log the result. Two changes were reverted this week for moving nothing.

## Numbers

**Real repositories — 2026-08-21.** Two tool changes, on the same model, against the 0/18 baseline:

| case | baseline | + `edit_file` | + contiguous reads |
|---|---|---|---|
| `real-rich` | 0/3 | 0/3 | **3/3** |
| `real-click` | 0/3 | 2/3 | **3/3** |
| `real-cachetools` | 0/3 | 2/2 | **2/3** |
| `real-humanize` | 0/3 | 0/2 | not re-run |
| `real-markdown` | 0/3 | — | **never run** |
| `real-more-itertools` | 0/3 | — | **never run** |

**Do not read a set-level percentage off this table.** Three of six cases have been measured with
both changes, and they are the same three that were tuned against — traces read, hypotheses formed
and changes judged on them all week. That is selection, and it is the risk the held-out set exists to
catch elsewhere. The gain is also concentrated: strip `real-rich` out and the second change shows
+1 run on `click` and −1 on `cachetools`.

What is solid is narrower and more useful: **`real-rich` went from impossible to reliable.** Fixing
`rich/console.py` once required emitting 25,308 tokens into a 16,000-token reply. It now takes ten
reads and a single edit.

The read-to-write ratio moved **1:29 → 1:7**. It stopped circling and started acting.

`real-humanize` still fails, and not for a reason any tool fixes: its edit applies cleanly and is
nearly correct, using `exponent += 3` where the fix needs `exponent += 3 - exponent % 3`. The carry
happens; the precision does not survive it. That is arithmetic, and it points at the model.

### The earlier reading, kept because it was wrong in an instructive way

**pass 4/7 (57%)** was quoted from three cases before `real-rich` was included; the honest figure at
that moment was 4/10.

At that point the set had left the saturated band every earlier set sits in — dev 93%, held-out 97%,
multibug 96% — which is what made a tuning cycle measurable here for the first time.

The clearest single case was `real-click`: it burned 255,043 tokens in the baseline and stalled at
6→1 of 6 failures. With `edit_file` it fixed **all six in two edits**, twice. Both passes carried
`verdict: compact` — the agent exhausted its budget and passed anyway, because scoring is by the
check command's exit code and never by the agent's own claim.

`real-rich` was still 0/3 at that stage, with all six of its edits rejected for *"that text appears
2 times"* — ambiguity in a 2,689-line file, not imprecision. That was read as evidence **against**
porting a fuzzy matcher, and it held up: the ambiguity failures disappeared once the agent could see
contiguous code, without ever being addressed directly. A cycle that attacked them head-on, by naming
the line numbers of each match, moved nothing and was reverted.

### The baseline this replaced

**Real repositories — 2026-08-20: pass 0/18.** Six real open-source projects, each vendored at the
parent of a genuine upstream bug-fix commit, scored by the repo's own test suite. 3 runs per case,
0 blocked, cap 30, egress restricted.

| case | files | pass | turns | tokens (med) | verdicts |
|---|---|---|---|---|---|
| `real-more-itertools` | 39 | 0/3 | 30/30/30 | 198,584 | stuck ×3 |
| `real-cachetools` | 41 | 0/3 | 28/20/25 | 245,713 | compact ×2 done ×1 |
| `real-humanize` | 73 | 0/3 | 10/20/1 | 86,422 | done ×2 compact ×1 |
| `real-click` | 156 | 0/3 | 28/27/26 | 255,043 | compact ×3 |
| `real-markdown` | 443 | 0/3 | 8/22/12 | 113,388 | done ×2 compact ×1 |
| `real-rich` | 548 | 0/3 | 29/26/25 | 249,883 | compact ×3 |

**This is the first case set this project owns with real headroom**, after three synthetic
difficulty axes were built and all three were rejected for saturating above 85%.

**The cause of 0/18 is measured, and it is a tool defect, not a reasoning one.** `write_file`
replaces a file entirely, so a five-line fix means emitting the whole file inside `MAX_TOKENS`
(16,000, covering thinking, text and tool arguments together). Real files are 559–2,689 lines:

| file | ~tokens to rewrite | share of one reply |
|---|---|---|
| `humanize/number.py` | 3,898 | 24% |
| `click/_termui_impl.py` | 7,933 | 50% |
| `more_itertools/recipes.py` | 11,531 | 72% |
| `rich/console.py` | **25,308** | **158% — impossible** |

Across 30 real-repository runs: **11 `write_file` calls against 352 `read_file` calls (1:32)**, nine
runs ended `stop_reason: "length"`, and every run that made progress made exactly **one** write.
`real-rich` cannot be passed by any agent using **this** toolset — a case that cannot be passed
measures nothing. *(Resolved: rather than dropping it, the toolset was fixed. With an edit tool the
arithmetic disappears, and `real-rich` now passes 3/3. Dropping it would have been lowering the bar
instead of clearing it.)*

The fix is an **edit tool** taking `old_string` → `new_string`. A budget experiment ruled out the
alternative explanation: given 1M tokens instead of 400k, the agent used 281–516k and made *less*
progress, because the binding limit is the per-reply cap, not the per-run budget.

**0/18 does not mean the agent cannot fix real bugs.** 13 of 18 runs (72%) ended on a *resource*
limit, not a decision: 10 `compact` (all at the 240,000-token threshold) and 3 `stuck` (all at the
30-turn cap). Only 5 ended because the agent chose to stop. On `humanize` it diagnosed the root
cause correctly and ran out of room before applying the fix.

**The `compact` verdicts look like they earn the compaction layer — they do not.** v1 deferred
compaction pending exactly this trigger (*"compact verdicts dominate the baseline distribution"*),
and `compact` never appeared once across 60 fixture runs against 10 of 18 here. So the trigger fired,
and it was tested directly: given 1M tokens instead of 400k, the agent used 281–516k and made *less*
progress. **Running out of budget is a symptom of the whole-file write, not an independent problem.**
The trigger earned a hypothesis; the experiment refuted it. Recorded so the next reader does not
build compaction on a distribution that has already been checked.

Cost: 3.54M tokens for 18 runs. The median case is 245,713 tokens against a 60,000 ceiling — real
repositories cost roughly 4× what v1 was scoped for. One run (`real-markdown` run 1) was interfered
with by hand to clear a hung process and is flagged as not a clean observation.

**Multibug set — 2026-08-19: pass 25/26**, 3 runs per case, cap 25, **4 blocked and excluded**.
Ten cases carrying 3, 4 or 5 independent one-line defects, all of which must be fixed for the suite
to go green.

| bugs | runs | pass | turns (range) | mean turns |
|---|---|---|---|---|
| 3 | 9 | 9/9 | 11–16 | 12.8 |
| 4 | 12 | 11/12 | 12–17 | 14.6 |
| 5 | 5 | 5/5 | 17–23 | 18.4 |

**This set was built to have headroom and does not.** The band was fixed before the data existed —
40–70% discriminates, above 85% means reject the axis — and 96% rejects it. That is **three
difficulty axes tried and three rejected**: misdirection, cross-cutting edits, and now independent
diagnoses. The search for a harder task shape ends here rather than continuing by guesswork.

What survived is more useful than the pass rate: **each additional bug costs about 2.8 turns**, the
first quantified predictive relationship in the project. It implies ~8 bugs would exceed a 25-turn
cap, and it shows the 12-turn cap used by the other two sets would have failed most of this one on
budget alone.

**Incomplete, and not to be quoted as 30.** `multi-catalogue` has zero completed runs and
`multi-payroll` two of three, so this is 9 of 10 cases; the free tier began rejecting ~2 of every 3
requests after ~1.1M tokens. Blocked runs are excluded because **a blocked run never reaches its
check command, so no score exists for it** — not because of anything their row reports, which is
written with `state=None` and therefore reads zero regardless. `--continue` completes the set.

**Held out — 2026-08-19: pass 29/30** on ten cases the system was never developed against,
scored once, 3 runs each, 0 blocked.

| Group | Score | The question it answers |
|---|---|---|
| Matched six | **17/18** | Was the dev score fitted to the dev cases? |
| Harder four | **12/12** | How much headroom is left? |

Dev is 14/15 (93.3%); matched held-out is 17/18 (94.4%) — **statistically indistinguishable, so the
dev score was not overfitted.** The reading was fixed in the plan before the data existed.

Two caveats stated rather than buried. **The four "harder" cases scored better than the matched
six** — the difficulty mechanism (the cause is not where the traceback points) was verified to work
but did not trouble the agent, so this set has little headroom either. And **every dev trace had
been read before these cases were written**, so the selection is not fully independent; all ten were
derived from a taxonomy of ordinary Python defects rather than from observed weaknesses, which is a
partial mitigation, not a complete one.

**Dev baseline — 2026-08-19, `nemotron-3-super-120b-a12b`: pass 14/15**, 3 runs per dev case, 0 blocked.

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

### Restricted egress

Scored runs reach the model through an allowlisting proxy and have **no other route off the
machine** - the agent container sits on an internal Docker network whose only neighbour is that
proxy. The harness brings it up automatically and **refuses to score a split without it**; set
`AGENT_NETWORK=bridge` to run unrestricted, in which case the result is not a compliant scored run.

Verified with the agent image: the model host returns `200`, any other host returns
`403 Filtered`, and a direct connection **by raw IP with no proxy** fails as unroutable. That last
check is the one that matters - a DNS-only barrier looks the same and is bypassed by dialling an IP.

### Interactive

```bash
docker run --rm -it -v "$PWD:/app" -v "$PWD/eval/workspace:/workspace"   --env-file .env personal-agent python -m agent "Fix the failing tests."

python -m agent --list              # past threads
python -m agent --resume <id>       # continue one
```

Destructive commands pause for approval and show the full argument set; a task's
identity is its thread id, so resuming is re-invocation rather than a restart.
