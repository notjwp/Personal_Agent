# Roadmap — a personal assistant that learns

The forward-looking half of the plan, kept in the repository so it is version-controlled alongside
the code it describes. `CONTEXT.md` remains the binding specification; `eval/CHANGELOG.md` is the
record of what was actually measured.

v1 is complete (Definition of Done 9/9). What follows is v2.

---

## v2 — a personal assistant that learns

**The goal, in your words:** an assistant that remembers past conversations across sessions, builds a
model of who you are, creates its own reusable skills after complex work, and can actually *do*
things through a real set of tools.

That reorders the old plan. It was a feature checklist; this is a spine with one theme. Every phase
below exists to serve **learning**, and each is gated on a number that says whether learning
happened.

```
K  sandbox      memory has nowhere to live until a second root is writable
L  tools        skills are procedures OVER tools; four tools is not enough to compose
M  memory       remembers across sessions, and can prove it
N  skills       writes its own procedures, retrieves them, and they help
```

`reset.sh` wipes the workspace every run by design, so anything that persists must live outside it.
That is why K comes first and is not optional.

### The hazard this whole spine is built against

**"It learns" is the easiest claim in AI to believe and the hardest to falsify.** You will feel it
working before it works. Every phase here therefore builds its measurement *before* its feature, and
ships with a switch that turns the feature off — because a capability that cannot be turned off
cannot be attributed either.

---

## Phase K — Two writable roots, kernel-enforced

**Context.** Memory and skills must survive `reset.sh`, which wipes the workspace every run by
design. They therefore need somewhere else to live, and that "somewhere else" must be as tightly
bounded as the workspace is — or the boundary that makes this project trustworthy quietly widens.

**A defect found while planning this phase, and it is the reason K is not just a mount:**

```
/workspace           writable   intended
/app                 WRITABLE   the entire project tree
/app/eval/fixtures   WRITABLE   the fixtures the agent is scored against
/usr/local           read-only  correct
```

`--read-only` makes the ROOT FILESYSTEM immutable; **bind mounts are unaffected**. The project is
bind-mounted at `/app`, so the agent can write to the harness, `tasks.jsonl`, and the fixtures that
decide its own score.

Nothing suggests it ever has — all 30 Phase I traces show zero access to `/app`, and the tamper check
restores protected test files after every run. But that guard is **post-hoc**: it repairs damage
rather than preventing it, and a successful write to `/app` would not even register as a violation,
because `count_write_violations()` looks for "Read-only file system" errors that would never occur.

**Exit criterion:** the agent home survives `reset.sh`; a write to the project tree is refused by the
kernel; the v1 suite score is unchanged.

### Task K1 — Make the project tree read-only

- [ ] **Step 1: Mount `-v {REPO}:/app:ro`** in `spawn()`.
- [ ] **Step 2: Two things currently write under `/app`, and both need a home first** — do not skip
      this and discover it mid-run:
      - `eval/runs/<ts>/` — the inner runner writes `summary.jsonl` and each trace there
      - `/app/.agent/state.db` — `AGENT_HOME` in the `Containerfile`
- [ ] **Step 3: Give each its own narrow writable mount**, so every writable path is declared rather
      than inherited:

      ```
      -v {REPO}:/app:ro                        the project, immutable
      -v {REPO}/eval/runs:/app/eval/runs       traces only
      -v {AGENT_HOME}:/state                   memory, skills, checkpoints
      -v {REPO}/eval/workspace:/workspace      the task
      ```

- [ ] **Step 4: Move `AGENT_HOME` to `/state`** in the `Containerfile`, so `STATE_DB` no longer lives
      inside the project. `config.py` already reads it from the environment — this is one line, and it
      is the single source of truth `FR-302`/`NFR-201` depend on.
- [ ] **Step 5: Prove it, in both directions.** A write to `/app`, to `/app/eval/fixtures`, and to
      `/app/eval/tasks.jsonl` must each fail with `Read-only file system`; writes to `/workspace`,
      `/state` and `/app/eval/runs` must each succeed. This is the same probe that found the defect.

### Task K2 — Teach the violation counter about the new shape

- [ ] **Step 1:** `count_write_violations()` currently detects refusals by their error text. With
      `/app` read-only, an attempted write there now **produces** that error, so it will start being
      counted — which is correct and is the point.
- [ ] **Step 2: Report which root was targeted**, not just a count. "Tried to write to the fixtures"
      and "tried to write to `/usr`" are different findings, and only one of them suggests the agent
      is trying to grade itself.
- [ ] **Step 3: Keep the tamper check.** Defence in depth: the kernel now prevents, and the tamper
      check still verifies. It has already caught one thing the kernel could not — a rig fault where
      an orphaned container corrupted a shared workspace.

### Task K3 — The agent home, and what lives in it

- [ ] **Step 1: `~/.personal-agent`** on the host, `/state` in the container. Created if absent, so a
      fresh clone works without ceremony.
- [ ] **Step 2: Layout, fixed now so M and N do not each invent one:**

      ```
      /state/state.db        checkpoints (moved from /app/.agent)
      /state/memory.db       Phase M, FTS5
      /state/skills/         Phase N, version-controlled text
      ```

- [ ] **Step 3: `reset.sh` must NOT touch it.** The whole point is that it survives. Add a test that
      writes a file to the agent home, runs `reset.sh`, and asserts the file is still there — the
      property is easy to break later and silent when broken.
- [ ] **Step 4: Per-run isolation for scored runs.** A memory that persists across scored case-runs
      would let case 2 benefit from case 1, which is the same contamination `missing-dep` forced
      one-container-per-run to prevent. **Scored runs get a fresh agent home per case-run; the
      interactive CLI keeps a persistent one.** Phase M's recall benchmark opts in deliberately,
      because carrying memory between sessions is precisely what it measures.

### Task K4 — Per-session egress

- [ ] **Step 1:** Repo work keeps no egress beyond the model. A task that needs the web declares it,
      and the allowlist is extended for that run only.
- [ ] **Step 2: Record the allowlist in the manifest**, so every number states the egress it was
      measured under — the same reason provider and model are recorded per row.
- [ ] **Step 3: The proxy machinery already exists** (Phase H) and derives its allowlist from the
      configured endpoint. This extends the derivation; it does not rebuild it.

### Task K5 — Prove nothing moved

- [ ] **Step 1:** `pytest -q` under the new mounts — no API key, no network, read-only root.
- [ ] **Step 2: Re-run the dev suite, 3 runs per case.** Expect 14/15. **This is a regression guard,
      not a measurement** — the score should not move, and if it does, the sandbox change is
      interfering and must be understood before Phase L starts.
- [ ] **Step 3: Record the probe output in `eval/CHANGELOG.md`** as evidence, with the commands, so a
      future reader can re-run it rather than trust a claim.

**Cost: no model quota for K1–K4** — the probes are shell commands. Only K5's regression run spends,
about 15 runs.

---

## Phase L — MCP: real tools, without dissolving the boundary

Four tools is enough to fix a bug and not enough to be an assistant. MCP is how that changes without
hand-writing forty schemas.

**Exit criterion:** MCP tools are callable, pass the policy gate, run inside the sandbox, and move
the real-repository number.

- [ ] **Step 1: `agent/registry.py`.** v1 deferred the `@tool` decorator to "tool six" on a measured
      break-even argument. With MCP that trigger fires.
- [ ] **Step 2: `agent/mcp.py`** — stdio transport first.
- [ ] **Step 3: MCP servers run INSIDE the sandbox.** This is the security crux: a server on the host
      with host access makes the container decorative. If a server cannot run inside, it is not used.
- [ ] **Step 4: Unknown tools stay DENIED by default.** `classify()` already refuses unknown names;
      that must survive dynamic registration rather than be loosened to accommodate it. Every server's
      tools get an explicit risk classification when registered, and anything `destructive` still
      pauses for approval interactively and is refused unattended.
- [ ] **Step 5: Measure.** If breadth does not move the number, that is a finding worth as much as the
      tools — and a reason to stop adding them.

---

## Phase M — Memory: remembers across sessions, and can prove it

**Exit criterion:** a measured improvement on a recall benchmark that was built and baselined
**before** any memory code existed.

- [ ] **Step 1: Build the recall benchmark FIRST.** Scripted multi-session runs: a fact established in
      one session, required in a later one, **in a different thread**. Within a thread the v1
      checkpointer already carries context, so a same-thread test measures the wrong thing entirely.
      Scored by whether the fact was retrieved — deterministic, no judge.
- [ ] **Step 2: Record the zero baseline from that harness**, before writing memory. v1 scores zero by
      construction; measuring it anyway is what makes the later delta real rather than assumed.
- [ ] **Step 3: The harness needs a session-chain concept.** Every run today is fresh and stateless.
      Multi-session evaluation is genuinely new rig work and should be costed as such.
- [ ] **Step 4: SQLite FTS5, keyword search only.** The spec forbids vectors until keyword recall is
      *measured and found wanting*. Honour it: Hermes uses FTS5 too, and a graph is where you may end
      up rather than where you start.
- [ ] **Step 5: Store what §4.3 already specifies** — decisions made, files touched, commands that
      worked, errors hit, artifact paths. That list was written for compaction and is exactly right
      for episodic memory. Nothing else.
- [ ] **Step 6: Write on `finish`, retrieve at `act`**, and cap the injected context hard.
- [ ] **Step 7: Report recall AND cost.** Every retrieved fact costs tokens, and on this budget memory
      that crowds out the task is a regression measured in tokens. A recall win paid for with a large
      token increase is a trade, and the trade must be visible.

**The model of you is derived from memory, not a second system.** A separate "user profile" is another
store to keep consistent and another thing to be wrong. What is measurable is narrower and more
useful: **does it stop asking what you have already answered.** That is a recall question, and the
benchmark above already answers it.

---

## Phase N — Skills: writes its own procedures, and they help

The most distinctive capability and the hardest to justify. Requires memory.

**Exit criterion:** a measured **reuse rate** AND a measured delta in success or cost when a skill is
reused versus solving cold. **Without both numbers this phase does not ship.**

- [ ] **Step 1: Build retrieval before authoring.** Writing a skill after a task is the easy half;
      recognising next week that this task resembles that one is the actual problem, and it is where
      such systems quietly fail. Build the retrieval path and the reuse counter first, so the reuse
      rate exists from the first skill.
- [ ] **Step 2: Skills are version-controlled text** in the agent home — readable, editable and
      deletable by a human. Not opaque blobs.
- [ ] **Step 3: A kill switch, and it is not optional.** Skills change behaviour invisibly across
      sessions, which makes every later measurement harder to attribute. Every scored run records
      whether skills were on, and the number must fall back to baseline when they are off.
- [ ] **Step 4: Self-improvement of skills is a LATER phase.** Hermes improves skills during use; that
      is a second capability with its own measurement, and bundling it here would make the first delta
      unattributable.

**The default outcome to design against:** a skill system that authors skills nobody ever retrieves.
It looks like success from the inside — files accumulate, the log fills — and changes nothing. The
reuse rate is what catches it.

---

## Deferred, and honestly so

| | Why it waits |
|---|---|
| **Scheduling** | A small worker over the sync loop. Real, but automation rather than learning |
| **Distribution** | Distributing an assistant not yet shown to learn produces support burden, not information |
| **Chat gateways** | The phase that would force an async service rewrite. Terminal-only was chosen deliberately |

---

## What this will and will not be

**Will:** an assistant with memory, real tools, self-written skills, and — unusually — numbers saying
whether any of it works. A recall figure that rises. A reuse rate. A cost per session.

**Will not:** Hermes. They have 40+ tools, seven execution backends, six chat surfaces and a funded
team shipping while this is built. Matching that surface is not the goal, and pretending otherwise
would be the same over-claim this project keeps having to retract.

**The honest bet:** an assistant whose learning you can point at is worth more than one with more
features and no evidence. That bet is itself measurable, which is the only reason it is worth making.

**And the constraint that outranks the plan:** the model. `real-humanize` fails today because the
agent writes `exponent += 3` where the fix needs `exponent += 3 - exponent % 3`. No phase here fixes
arithmetic. A stronger model moved this project 4/15 → 14/15 with zero code changes — more than any
phase below is likely to.

---

## Verification

```bash
# 1. Unit tests — still no API key, no network, read-only root
docker run --rm --network none --read-only --tmpfs /tmp:exec -v "$PWD:/app" personal-agent pytest -q

# 2. v1 regression — the existing sets must not move as v2 lands
python eval/harness.py --split dev --runs 3 --pace 20

# 3. Real-repo verification — BOTH directions, offline, zero quota (Phase J3)
MSYS_NO_PATHCONV=1 docker run --rm --network none -v "$PWD:/app" \
  -v "$PWD/eval/workspace:/workspace" personal-agent bash -c \
  'scripts/reset.sh real-<name> && cd /workspace && <check>; echo "exit=$?"'

# 4. Rig preflight on a free local model — find rig bugs before spending quota (Phase J4)
AGENT_PROVIDER=openai NIM_BASE_URL=http://host.docker.internal:11434/v1 \
  python eval/harness.py --case real-<name> --runs 1

# 5. The scored baseline — the number that actually matters now (Phase J5)
python eval/harness.py --split real --runs 3 --pace 20
python eval/harness.py --split real --runs 3 --pace 20 --continue   # next day

# 6. Recall benchmark (Phase M — built before the memory code, baseline recorded at zero)
python eval/harness.py --split recall --runs 3

# 7. Clean-machine install (Phase P) — a container that has never seen the repo
docker run --rm -it ubuntu:24.04 bash -c "curl -fsSL <url>/install.sh | sh && personal-agent 'fix the failing test'"
```

On Git Bash, prefix any `docker run` carrying a `-v` mount with `MSYS_NO_PATHCONV=1`.

**A requirement is satisfied when the cases exercising it pass, not when the code exists** (§8.1).
That rule does not relax for v2 — it is the only thing separating this plan from a feature list.
