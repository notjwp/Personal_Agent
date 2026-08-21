================================================================================
PERSONAL AGENT — BUILD SPECIFICATION
Agent-facing prompt / project context document
================================================================================

--------------------------------------------------------------------------------
0. HOW TO USE THIS FILE
--------------------------------------------------------------------------------

Paste this whole file as project context into a coding agent (Claude Code,
Antigravity, Cursor) or attach it as CONTEXT.md at the repo root.

Read sections 1-4 to understand the system. Read section 9 to know what to build
FIRST. Sections 5-7 are the full requirement set for the finished system and are
NOT a build order — do not implement them top to bottom.

Rules for the implementing agent:
  - Section 9 is the only build order. Anything marked [S] or [C] in sections
    6-7 is out of scope until the [M] set passes evaluation.
  - Section 12 lists every file that may exist in v1. Do not create others.
  - Section 13 is binding. Violating a CE rule is a defect, not a style choice.
  - Every deterministic node must be unit-testable without an API key.
  - When a requirement and existing code disagree, the requirement wins; say so
    rather than silently reinterpreting it.


--------------------------------------------------------------------------------
1. PROJECT DESCRIPTION
--------------------------------------------------------------------------------

A single-user autonomous agent that runs on the owner's personal machine. It
accepts a goal in natural language, decomposes it into steps, and works the
steps by calling tools — reading and writing files, running shell commands and
Python, operating git, searching and reading the web — looping until the goal is
met, it gets stuck, or it exhausts its budget.

Three properties separate this from a chat wrapper with function calling:

  (a) EVERY tool call passes a deterministic policy gate before any side effect.
      Nothing destructive runs unapproved. In unattended mode, anything that
      would need approval is refused and queued for later review rather than
      guessed at.

  (b) NO tool output reaches the model unfiltered. Results are truncated, the
      full output is spilled to an artifact file, and the model gets a summary
      plus a path it can re-read selectively. This is what allows tasks to run
      past ~15 turns without the context window collapsing.

  (c) State is checkpointed after every node transition. A killed process loses
      at most one node of work, and a task resumes rather than restarts.

Target environment: Fedora Linux natively, Windows via WSL2. Execution is
confined to a container with a single bind-mounted workspace directory.

Success is measured, not asserted: a fixture suite of repo-repair tasks is run
headlessly and scored by exit code. The pass rate is the project's headline
number.


--------------------------------------------------------------------------------
2. ARCHITECTURE
--------------------------------------------------------------------------------

                         +----------------------+
                         |  INTERFACE           |
                         |  TUI / CLI, streamed |
                         +----------+-----------+
                                    |
                         +----------v-----------+
                         |  SCHEDULER + QUEUE   |
                         |  cron, task rows     |
                         +----------+-----------+
                                    |
               +--------------------v--------------------+
      +------->|  ORCHESTRATOR                           |
      |        |  plan . act . reflect                   |
      |        |  checkpointer -> /state/state.db        |
      |        +--------------------+--------------------+
      |                             |
      |                  +----------v-----------+        +---------------+
      |                  |  POLICY GATE         |<------>| HUMAN         |
      |                  |  auto / confirm/deny |        | approve, edit |
      |                  +----------+-----------+        +---------------+
      |                             |
      |          +------------------+------------------+
      |          |                  |                  |
      |    +-----v------+    +------v------+    +------v------+
      |    | SANDBOX    |    | WEB         |    | MEMORY      |
      |    | shell      |    | search      |    | SQLite+FTS5 |
      |    | python     |    | fetch       |    | AGENT.md    |
      |    | files, git |    | browser     |    | artifacts   |
      |    +-----+------+    +------+------+    +------+------+
      |          |                  |                  |
      |          +------------------+------------------+
      |                             |
      |                  +----------v-----------+
      +------------------+  CONTEXT MANAGER     |
                         |  truncate . spill    |
                         |  . compact           |
                         +----------------------+

Layers the naive design omits, and why each exists:

  POLICY GATE      The model will eventually emit `rm -rf`, `git push --force`,
                   or a pip install into system Python. Classify before running.

  CONTEXT MANAGER  A single `ls -R` over node_modules or a 3000-line file read
                   ends the task. Bound every result on the way in.

  SANDBOX          "Terminal" is not a tool, it is an execution environment with
                   an enforced workspace root.

  SCHEDULER SPLIT  The chat process must not be the execution process, or you
                   cannot detach from a task that has been running 40 minutes.


--------------------------------------------------------------------------------
3. ORCHESTRATION CONTROL FLOW
--------------------------------------------------------------------------------

                            START
                              |
                              v
                          +--------+
              +---------->|  PLAN  |  decompose goal into 2-6 steps
              |           +---+----+
              |               |
              |               v
              |           +--------+
              |    +----->|  ACT   |  LLM emits tool_use blocks OR final text
              |    |      +---+----+
              |    |          |
              |    |    tool_use? --- no ---------------------+
              |    |          |                               |
              |    |         yes                              |
              |    |          v                               |
              |    |      +--------+   confirm   +-------------------+
              |    |      |  GATE  |------------>|  AWAIT APPROVAL   |
              |    |      +---+----+<------------+  (interactive)    |
              |    |     auto |                  +-------------------+
              |    |          |   deny -> synthetic error observation
              |    |          v
              |    |      +---------+
              |    |      | EXECUTE |  run approved tools, catch exceptions
              |    |      +---+-----+
              |    |          |
              |    |          v
              |    |      +---------+
              |    |      | OBSERVE |  truncate, spill, emit tool_result blocks
              |    |      +---+-----+
              |    |          |                                |
              |    |          v                                |
              |    |      +---------+<-------------------------+
              |    |      | REFLECT |  deterministic checks only
              |    |      +---+-----+
              |    |          |
              |    |  continue|
              |    +----------+
              |               |
              |    compact ---+---> [ COMPACT ] ---> back to ACT
              |               |
              +--- replan ----+
                              |
                     done | stuck
                              |
                              v
                          +--------+
                          | FINISH |----> END
                          +--------+

Node classification:

  LLM CALLS (3)          plan, act, compact-summarisation, and the reflect
                         node's optional tie-break (default: none)
  DETERMINISTIC (5)      gate, execute, observe, reflect, finish
  HUMAN                  await approval, reachable only from gate

Only three nodes touch the model. Everything else is ordinary code you can unit
test. This ratio is the single most important design property of the system.


--------------------------------------------------------------------------------
4. WORKFLOW / EXECUTION SEMANTICS
--------------------------------------------------------------------------------

4.1 TURN LIFECYCLE

  1. A goal arrives from chat or from the task queue.
  2. PLAN produces an ordered step list. Cursor set to 0.
  3. ACT calls the model with the full message history, the tool schemas, and
     the current step injected into the system prompt (NOT appended as a user
     message — that pollutes history and creates gaps).
  4. Router inspects the last assistant message:
       - contains tool_use blocks  -> GATE
       - text only                 -> REFLECT (step complete, not termination)
  5. GATE classifies each tool call independently. Mixed verdicts in one turn
     are legal: some calls approved, others denied.
  6. EXECUTE runs approved calls. Tool exceptions are caught and converted to
     observations; they never propagate out of the node.
  7. OBSERVE shrinks each result, spills overflow to .agent/artifacts/<id>.txt,
     and appends tool_result blocks as a single user message.
  8. REFLECT emits exactly one verdict. Checks run in this fixed order:
       a. spent_tokens > 60% of budget          -> compact
       b. turns >= max_turns                    -> stuck
       c. last 3 tool signatures identical      -> stuck
       d. 3 consecutive failures                -> replan
       e. last message is assistant text        -> done if final step,
                                                   else continue + advance cursor
       f. otherwise                             -> continue
  9. Terminal verdicts route to FINISH, which writes durable memory and ends.

4.2 APPROVAL SEMANTICS

  Interactive mode: a `confirm` verdict suspends the graph. The user sees the
  exact command or argument set, unabbreviated, and approves or rejects.

  Autonomous mode: `confirm` becomes `deny`. The request is recorded in a review
  queue. The agent receives a denial observation and must find another route.
  The same policy code serves both modes — do not fork it.

  CRITICAL RE-ENTRANCY RULE: the gate node re-executes from its first line when
  execution resumes after approval. It must therefore be free of side effects.
  No logging, no counter increments, no database writes inside gate. This rule
  applies to any node placed upstream of a suspension point.

4.3 CONTEXT DISCIPLINE

  - Each tool declares its own output cap. Shell gets more than file writes.
  - Over-cap results are written to disk in full; the model receives head lines,
    an elision marker, tail lines, and the artifact path.
  - The spill message must tell the model how to inspect further (read_file with
    offset/limit, or grep against the path). A path with no instruction is
    ignored by the model in practice.
  - Compaction preserves the first two messages and the last six verbatim,
    summarising only the middle. The summary retains: decisions made, files
    touched, commands that worked, errors hit, artifact paths. Nothing else.

4.4 PERSISTENCE

  - Checkpoint after every node transition, keyed by thread_id.
  - A task's identity is its thread_id. Resume is re-invocation with the same id.
  - Chat threads and background tasks share the checkpoint store. A chat session
    attaching to a running task is just a reader on the same thread.

4.5 EVALUATION LOOP

  1. Reset workspace to the fixture's known state.
  2. Invoke the graph in autonomous mode (so nothing blocks on approval).
  3. Run the fixture's check command. Exit code 0 is a pass.
  4. Record: pass, turns, tool calls, tokens, seconds, terminal verdict.
  5. Append to a timestamped run file. Report the delta against the prior run.


--------------------------------------------------------------------------------
5. USER REQUIREMENTS
--------------------------------------------------------------------------------

Written in the owner's voice. These state what the system is for, not how it
works.

  ID      Tier       As the owner of the machine, I want...
  ------  ---------  ----------------------------------------------------------
  UR-01   Core       one place to talk to the agent, where it recalls earlier
                     sessions
  UR-02   Core       it to state a plan before acting on anything non-trivial
  UR-03   Core       it to work a multi-step task without me re-prompting each
                     step
  UR-04   Core       it to recover from its own errors, not halt at the first
                     failure
  UR-05   Core       to see what it is about to do, and be able to stop it
  UR-06   PC         it to read, create and modify files in a project I point at
  UR-07   PC         it to run shell commands and Python on my machine
  UR-08   PC         it to install dependencies a task needs
  UR-09   PC         it to perform git and GitHub operations
  UR-10   PC         it confined to a workspace so it cannot touch the rest of
                     my system
  UR-11   Web        it to search the web and read pages
  UR-12   Web        it to drive a real browser when a page needs interaction
  UR-13   Autonomy   to hand off a long task and check back later
  UR-14   Autonomy   to schedule recurring tasks
  UR-15   Autonomy   an interrupted task to resume where it stopped, not restart
  UR-16   Autonomy   to review anything risky it queued while I was away


--------------------------------------------------------------------------------
6. FUNCTIONAL REQUIREMENTS
--------------------------------------------------------------------------------

Priority: [M] Must  [S] Should  [C] Could  [W] Won't (this release)
Only [M] items are in scope for the first build. See section 9.

6.1 ORCHESTRATION (FR-1xx)

  FR-101  [M]  Decompose a goal into an ordered step list before executing.
  FR-102  [M]  Invoke tools via the model's native tool-calling interface.
               Never parse tool calls out of free text.
  FR-103  [M]  Loop act -> gate -> execute -> observe -> reflect until a
               terminal verdict is reached.
  FR-104  [M]  Terminate on exactly one of: done, stuck, budget exhausted,
               turn cap reached.
  FR-105  [M]  Advance the plan cursor on step completion and record step
               status.
  FR-106  [S]  Detect three consecutive identical tool invocations and
               terminate as stuck.
  FR-107  [S]  Re-plan after three consecutive tool failures rather than
               continuing blind.
  FR-108  [M]  Persist complete state to a checkpoint after every node
               transition.

6.2 TOOLS AND EXECUTION (FR-2xx)

  FR-201  [M]  Read files with offset/limit, write files, list directories,
               all rooted at the workspace.
  FR-202  [M]  Run shell commands with a timeout, capturing exit code, stdout
               and stderr separately.
  FR-203  [M]  Execute Python, returning stdout, tracebacks, and the value of
               the final expression.
  FR-204  [S]  Install packages into the sandbox environment on request.
  FR-205  [S]  Perform git status, diff, branch, add, commit, push.
  FR-206  [S]  Provide repository inspection (tree, grep, symbol search) that
               returns paths and line numbers, not file contents.
  FR-207  [M]  Register a new tool by decorating one function; derive its JSON
               schema automatically from the signature and docstring.
  FR-208  [M]  Return tool exceptions to the model as observations rather than
               propagating them out of the node.

6.3 POLICY AND APPROVAL (FR-3xx)

  FR-301  [M]  Classify every tool call as auto, confirm, or deny before any
               side effect occurs.
  FR-302  [M]  Reject any argument whose resolved path falls outside the
               workspace root.
  FR-303  [M]  Suspend execution and await human input on a confirm verdict in
               interactive mode.
  FR-304  [M]  Downgrade confirm to deny in autonomous mode and record the
               request for later review.
  FR-305  [M]  Evaluate policy with no side effects, so the node is safe to
               re-enter on resume.
  FR-306  [M]  Present the exact command or argument set in the approval
               prompt, unabbreviated and untruncated.
  FR-307  [C]  Allow the user to amend a tool call's arguments at the approval
               point, not merely accept or reject.

6.4 CONTEXT AND MEMORY (FR-4xx)

  FR-401  [M]  Truncate any tool result exceeding its per-tool cap before it
               enters context.
  FR-402  [M]  Write the untruncated result to an artifact file and return its
               path, plus instructions for inspecting it.
  FR-403  [M]  Compact conversation history when context use exceeds a
               configured fraction of budget.
  FR-404  [M]  Preserve the opening messages and the most recent turns verbatim
               through compaction.
  FR-405  [M]  Persist conversation history per thread and restore it across
               process restarts.
  FR-406  [S]  Maintain a durable user profile file loaded into every session.
  FR-407  [S]  Provide keyword retrieval over episodic memory.
  FR-408  [C]  Provide semantic retrieval, gated on a measured shortfall in
               keyword recall. Do not build this speculatively.

6.5 WEB (FR-5xx)

  FR-501  [M]  Perform web search returning ranked results with titles and URLs.
  FR-502  [M]  Fetch a URL and extract main content as clean text, discarding
               navigation and boilerplate.
  FR-503  [S]  Navigate, click, fill and submit forms in a real browser.
  FR-504  [S]  Serialise pages as an accessibility tree rather than raw DOM.
  FR-505  [S]  Honour robots.txt and apply per-domain rate limiting.

6.6 AUTONOMY (FR-6xx)

  FR-601  [M]  Accept a task into a queue and return a task identifier
               immediately.
  FR-602  [M]  Execute queued tasks in a worker process independent of any chat
               session.
  FR-603  [M]  Resume an interrupted task from its last checkpoint without
               repeating completed work.
  FR-604  [M]  Report task status as queued, running, awaiting-approval, done,
               or failed.
  FR-605  [S]  Trigger tasks on a cron schedule.
  FR-606  [S]  Allow a chat session to attach to and detach from a running task.
  FR-607  [S]  Cap the number of concurrent workers.

6.7 INTERFACE (FR-7xx)

  FR-701  [M]  Provide a CLI/TUI chat with streamed output.
  FR-702  [M]  Display the current plan and active step at all times.
  FR-703  [M]  List threads and tasks; resume either by identifier.
  FR-704  [W]  Accept voice input and produce speech output.

6.8 EVALUATION AND OBSERVABILITY (FR-8xx)

  FR-801  [M]  Run a fixture task set headlessly and record per-case pass/fail.
  FR-802  [M]  Reset the workspace to a known state before each case.
  FR-803  [M]  Record turns, tool calls, tokens and wall time per case.
  FR-804  [S]  Append results to a versioned run file and report the delta
               against the previous run.
  FR-805  [M]  Emit one structured log line per tool call.


--------------------------------------------------------------------------------
7. NON-FUNCTIONAL REQUIREMENTS
--------------------------------------------------------------------------------

A requirement without a number is not testable. Targets are the point.

  ID        Attribute        Requirement and target
  --------  ---------------  ------------------------------------------------
  NFR-101   Latency          First assistant token within 3 s at p50 on an
                             interactive turn
  NFR-102   Overhead         Framework cost per loop iteration <= 250 ms,
                             excluding model and tool time
  NFR-103   Latency          Checkpoint write <= 50 ms at p95
  NFR-104   Context          No single tool result exceeds 2,000 tokens after
                             shrinking
  NFR-201   Safety           Zero writes outside the workspace root across the
                             full eval suite
                             AMENDED 2026-08-21 (Phase K): exactly TWO declared
                             writable roots - the workspace, and the agent home
                             holding checkpoints, memory and skills. Memory has
                             to survive reset.sh, which wipes the workspace by
                             design, so it cannot live inside it. Everything
                             else is refused BY THE KERNEL, the project tree
                             included, and each refusal is recorded with the
                             path it targeted. Reason and evidence: ROADMAP.md
                             Phase K, eval/CHANGELOG.md.
  NFR-202   Safety           No destructive command executes unapproved in
                             interactive mode; none at all in autonomous mode
  NFR-203   Security         Secrets never enter model context; env-var
                             indirection plus output redaction
  NFR-204   Isolation        All execution in a container with no host mounts
                             besides the workspace
  NFR-205   Security         Sandbox network egress restricted to a configured
                             domain allowlist
  NFR-301   Recoverability   A process kill at any instant loses at most one
                             node of work
  NFR-302   Correctness      Resume produces no duplicated side effects
  NFR-303   Resilience       Transient provider errors retried with exponential
                             backoff, three attempts maximum
  NFR-304   Boundedness      Every task carries enforced caps on turns, tokens
                             and wall-clock time
  NFR-401   Cost             Token budget enforced as a hard stop, not advisory
  NFR-402   Cost             Median eval case completes within 60,000 tokens
  NFR-403   Cost             Compaction reduces context by at least 50% when it
                             fires
  NFR-501   Observability    Every tool call logs name, argument hash, verdict,
                             duration, byte counts, spill path
  NFR-502   Auditability     Any completed task is reconstructible from logs
                             alone
  NFR-601   Extensibility    Adding a tool requires editing exactly one file
  NFR-602   Testability      Every deterministic node is unit-testable without
                             an API key
  NFR-603   Maintainability  Prompts live in version-controlled files, not
                             string literals
  NFR-701   Portability      Runs on Fedora natively and on Windows under WSL2
  NFR-702   Portability      Model provider swappable behind a single adapter
  NFR-703   Independence     No hosted service required for state; local SQLite
  NFR-801   Usability        Approval or rejection resolvable in one keystroke
  NFR-802   Transparency     All agent artifacts under one inspectable directory


--------------------------------------------------------------------------------
8. TRACEABILITY AND KNOWN CONFLICTS
--------------------------------------------------------------------------------

8.1 REQUIREMENT -> COMPONENT

  FR-101, 104-107      reflect / plan nodes            graph.py
  FR-102, 103, 108     graph wiring + checkpointer     graph.py
  FR-201..206, 208     the tool functions              tools.py
  FR-207              schema derivation               registry.py (deferred,
                                                       see section 12)
  FR-301..307          gate node + classify()          graph.py, policy.py
  FR-401..404          shrink() at the execute node    context.py, graph.py
  FR-405               checkpointer                    graph.py
  FR-501..505          web tools                       web.py (deferred)
  FR-601..607          queue and worker                worker.py (deferred)
  FR-701..703          entrypoint                      cli.py
  FR-801..805          eval harness                    eval/harness.py

Verification for the FR-1xx and FR-2xx families is the eval harness. A
requirement is satisfied when the cases exercising it pass, not when the code
exists.

8.2 CONFLICTS — stated, not papered over

  UR-13 vs UR-05
    Unattended operation and per-action visibility are mutually exclusive.
    Resolution: FR-304. Autonomous mode denies rather than pauses and defers to
    a review queue. Less gets done unattended; nothing catastrophic happens.

  NFR-104 vs FR-208
    Truncating tool output loses information needed to diagnose an error.
    Resolution: FR-402 spill-and-path. Costs one extra tool call, buys a
    bounded context.

  FR-503 vs NFR-402
    Browser automation is the largest token consumer in the system and will
    breach the cost target alone. This is why it sits at [S] behind FR-501/502:
    search plus text extraction covers most real requests at a fraction of the
    spend.


--------------------------------------------------------------------------------
9. BUILD SEQUENCE — THE ONLY ORDER THAT MATTERS
--------------------------------------------------------------------------------

Do not implement sections 5-7 in order. Build this instead.

TARGET LOOP FOR V1: given a repository with a failing test, make it pass.
Bounded, scored by exit code, needs no human judgement, and recognisable to
anyone reviewing the project.

Each step below has an EXIT CRITERION. Do not begin the next step until it
holds. If a step overruns its estimate by more than double, stop and reduce
scope rather than pushing through.

................................................................................
  STEP 1 — FIXTURES AND A NULL AGENT                              ~1.5 days
................................................................................

  Goal: prove the measurement rig works before there is anything to measure.

  Deliverables
    Containerfile          python:3.12-slim + git + pytest. No agent code.
    scripts/reset.sh       takes a case id, restores /workspace to that case's
                           known-broken state. Must be idempotent: running it
                           twice in a row produces the identical tree.
    eval/fixtures/<id>/    the broken repo for each case, committed to git
    eval/tasks.jsonl       5 dev cases (schema in section 12):
                             fix-import       a broken relative import
                             add-endpoint     a missing route plus its test
                             off-by-one       a range bug in a pure function
                             broken-fixture   a pytest fixture with a bad yield
                             missing-dep      an uninstalled package
    eval/harness.py        runner and scorer
    agent/graph.py         a NULL AGENT: `app.invoke(...)` returns the input
                           state unchanged. No model call. No tools.

  Why the null agent: eval/harness.py imports `app` from graph.py. Without a
  stub you cannot execute the harness at all, so a 0/5 in step 3 is ambiguous
  between "the agent failed" and "reset.sh is broken". The stub removes that
  ambiguity permanently.

  EXIT CRITERION
    Harness runs end to end and prints `pass 0/5`. For every case the setup
    command exits 0 and the check command exits non-zero. Delete a fixture's
    bug by hand, re-run, and that case flips to pass — this confirms the check
    command actually discriminates rather than always failing.

  Traps
    - A check command that fails for the wrong reason (collection error, not
      assertion failure) will mask a working agent later. Verify each check
      fails on the intended assertion.
    - reset.sh must remove untracked files, not just `git checkout`. The agent
      will create files.

................................................................................
  STEP 2 — THE THINNEST LOOP THAT RUNS                            ~1 day
................................................................................

  Goal: the smallest thing that can pass a case.

  In scope
    Nodes:        act -> gate -> execute -> observe -> reflect
    Tools:        read_file, write_file, run_shell
    Checkpointer: yes. One line, and it is how you inspect failed runs in
                  step 4. MemorySaver is acceptable for v1; SqliteSaver is
                  better and no harder.
    Tracing:      per-case full message dump (see below)

  Explicitly NOT in scope
    plan node, compact node, memory, web, scheduler, worker, TUI

  Required code changes from the skeleton
    (a) Rewire the entry edge: START -> "act", not START -> "plan".

    (b) TERMINATION GUARD. With no plan node, `plan` is [] and `cursor` is 0,
        so the reflect check `cursor + 1 >= len(plan)` evaluates `1 >= 0` and
        returns `done` on the FIRST text-only reply — including "Let me look at
        the test file first." Replace with:

            if s["messages"][-1]["role"] == "assistant":
                made_a_call = any(b["type"] == "tool_use"
                                  for m in s["messages"] if m["role"] == "assistant"
                                  for b in m["content"])
                return {"verdict": "done" if made_a_call else "continue"}

        Restore the cursor-based check only when the plan node is added.

    (c) FAILURE COUNTER. `failures` as Annotated[list, add] never resets —
        `plan` returning {"failures": []} appends an empty list, it does not
        clear. So `len(failures) >= 3` latches true for the rest of the run.
        Change to a plain int with overwrite semantics counting CONSECUTIVE
        failures: observe sets it to 0 when every result succeeded, otherwise
        increments it.

    (d) RISK MAP. `run_shell` is declared risk="destructive" but classify()
        special-cases the name and returns before consulting RISK, so the
        declaration is dead. Any future tool marked destructive would be
        silently auto-denied in autonomous mode and never run during eval.
        Either relabel run_shell as "write", or make RISK the single path.

    (e) TRACING (FR-805 scoped in early, not deferred). The harness writes
        eval/runs/<timestamp>/<case-id>.json containing the full final message
        list plus per-call metadata: tool name, verdict, duration, input bytes,
        output bytes, spilled path. Step 4 is unactionable without this.

  EXIT CRITERION
    At least one dev case passes. Trace files exist and are readable for all
    five. A deliberately introduced tool exception appears as an observation
    in the trace rather than crashing the run.

................................................................................
  STEP 3 — BASELINE                                               ~1 hour
................................................................................

  Goal: a number with an error bar, not a number.

  Run the 5 dev cases at 3 seeds each = 15 invocations. Record per case:
  pass count out of 3, median turns, median tokens, terminal verdict
  distribution.

  Expect 1/5 to 2/5. That is the correct baseline and not a problem.

  EXIT CRITERION
    A committed baseline row: pass rate, variance across seeds, median turns,
    median tokens. Everything after this is measured as a delta against it.

  Trap
    A case that passes 1 of 3 seeds is not a passing case. Report pass counts,
    never a single run.

................................................................................
  STEP 4 — ONE FIX PER CYCLE                                      repeat
................................................................................

  Goal: change one thing, measure, keep or revert.

  Read the traces. Classify every failure into exactly one bucket, then fix
  only the largest bucket.

    SYMPTOM IN TRACE                          BUCKET          FIX
    ----------------------------------------  --------------  ---------------
    One tool_result dominates the transcript  context flood   FR-401/402
    Same argument hash 3+ times in a row       thrashing       FR-106
    Verdict `stuck` at max_turns, no repeats   no strategy     plan node
    Edits a file it never read                 blind editing   prompt (SOUL.md)
    Runs pytest once, never re-runs            no verify loop  prompt (SOUL.md)
    Verdict `done` with the test still failing termination bug  reflect logic
    Tool exception repeated verbatim           bad error text  tool return value

  Rules for this step
    - One change per cycle. Two changes and you cannot attribute the delta.
    - Prompt changes count as changes and must be measured like code changes.
    - Revert anything that does not move the number. Keeping a neutral change
      because it "seems right" is how the loop rots.
    - Log each cycle in eval/CHANGELOG.md: hypothesis, change, before, after,
      kept or reverted.

  EXIT CRITERION per cycle
    Baseline re-run at 3 seeds, delta recorded, decision logged.

................................................................................
  STEP 5 — HOLD-OUT AND STABILISE                                 ~2 days
................................................................................

  Goal: confirm you improved the agent rather than the five dev cases.

  Write 10 further cases and DO NOT look at their traces during step 4. Run
  them only at milestones. Five cases means one flip is a 20% swing, and
  tuning against five overfits to five.

  EXIT CRITERION
    Dev set stable at >= 4/5 across 3 seeds, and the held-out 10 run once with
    the result recorded — whatever it is. A held-out score well below the dev
    score is information, not failure: it tells you which fixes were general.

................................................................................
  AFTER V1 — LAYER ORDER
................................................................................

  Add one layer at a time. Each must be justified by a measured eval delta or
  by a requirement it unblocks; do not fix the order in advance.

  Likely order on repo-repair tasks, with the reasoning:
    1. compaction     a 12-turn bug fix does not need decomposition, it needs
                      to not drown in file contents
    2. plan node      earns its place only once tasks span multiple files
    3. resume/SIGKILL exercises the checkpointer already present from step 2
    4. memory         needs episodic recall to have something worth recalling
    5. web            largest token cost, smallest v1 benefit
    6. scheduler      pure infrastructure, no effect on the pass rate

  The stated order is a prediction, not a plan. If compaction moves nothing and
  the plan node moves 20 points, the prediction was wrong and the numbers win.


--------------------------------------------------------------------------------
10. DEFINITION OF DONE (V1)
--------------------------------------------------------------------------------

  [ ] 5 dev cases + 10 held-out cases in eval/tasks.jsonl
  [ ] Dev set >= 4/5 across 3 seeds; held-out set scored at least once and the
      number recorded, whatever it is
  [ ] Zero writes outside the two declared roots across the full suite
      (NFR-201, as amended)
  [ ] Median case completes within the turn and token caps (NFR-402)
  [ ] Every deterministic node has unit tests that run without an API key
  [ ] A SIGKILL mid-task, followed by resume, completes without duplicated
      side effects (NFR-302)
  [ ] Adding a new tool touches exactly one file (NFR-601)
  [ ] eval/runs/ contains at least three dated runs, each with per-case trace
      files, showing the improvement trajectory
  [ ] eval/CHANGELOG.md records every tuning cycle: hypothesis, change,
      before, after, kept or reverted


--------------------------------------------------------------------------------
11. NON-GOALS
--------------------------------------------------------------------------------

  - Multi-user support, auth, tenancy.
  - Multi-agent orchestration, sub-agent spawning, agent-to-agent protocols.
  - A web UI. CLI/TUI only.
  - Voice (FR-704 is explicitly [W]).
  - A general-purpose plugin marketplace or dynamic tool loading.
  - Vector search before keyword recall has been measured and found wanting.
  - Fine-tuning or local model hosting for the orchestrator.
  - Windows-native support outside WSL2.


--------------------------------------------------------------------------------
12. REPOSITORY LAYOUT
--------------------------------------------------------------------------------

This is the V1 layout, not the finished system. Create these files and no
others. A file that exists before its layer is earned will be filled with
speculative code and will rot.

  personal-agent/
    pyproject.toml       dependencies, package metadata
    Containerfile        sandbox image
    .gitignore           must include .agent/
    README.md            baseline and current numbers table
    agent/
      __init__.py
      config.py          SINGLE source of truth: WORKSPACE root, MODEL, per-
                         tool output caps, turn/token budgets, compaction
                         threshold, head/tail line counts
      tools.py           the three v1 tool functions plus their hand-written
                         schemas, nothing else
      policy.py          classify() — the gate's entire logic, no side effects
      context.py         shrink()
      graph.py           AgentState, node functions, graph wiring
      cli.py             entrypoint: python -m agent "goal"
    prompts/
      SOUL.md            system prompt, version controlled (NFR-603)
    tests/
      test_policy.py     path escape, danger regex, mode downgrade
      test_context.py    under cap, over cap, spill path present in output
      test_reflect.py    every verdict branch
    eval/
      harness.py         runner and scorer
      tasks.jsonl        fixture cases (5 dev + 10 held-out, flagged by field)
      fixtures/<id>/     the broken repo for each case, committed
      runs/<ts>/         summary.jsonl + <case-id>.json full traces
      CHANGELOG.md       one row per tuning cycle
    scripts/
      reset.sh           restore workspace to a fixture's known state
    .agent/              RUNTIME STATE, gitignored
      artifacts/         spilled tool output (inside the workspace: the model
                         has to be able to read a spill without tripping FR-302)
      egress/            proxy config and the allowlist a run was measured under
      homes/<case>-<n>/  a BLANK agent home per scored case-run

  ~/.personal-agent/     THE AGENT HOME, mounted at /state (amended NFR-201).
    state.db             checkpoints
    AGENT.md             durable user profile, written by the agent
                         Outside the project tree, which the container now
                         mounts read-only, and outside the workspace, which
                         reset.sh wipes between runs.

DEFERRED FILES — create each only when its layer is earned

  agent/registry.py    the @tool decorator and schema derivation. Break-even
                       against hand-written schemas is five tools; v1 has
                       three. Add at tool six.
  agent/memory.py      when episodic recall has something worth recalling
  agent/web.py         when FR-501/502 enter scope
  agent/worker.py      when FR-6xx enters scope. NOT in v1 — section 9 lists
                       the scheduler and worker as explicitly out of scope.

RESOLVED DEFECTS FROM THE PREVIOUS LAYOUT

  - state.py folded into graph.py. One caller, one implementation.
  - The workspace root was defined independently in two modules. FR-302 and
    NFR-201 both depend on those agreeing. It now lives in config.py alone.
  - AGENT.md moved out of prompts/. It is runtime-mutable state the agent
    writes; keeping it under a version-controlled prompts directory forces
    either committed agent scribbles or a gitignored file inside a tracked
    folder.
  - tests/ added. NFR-602 requires it and nothing previously satisfied it.
  - cli.py added. FR-701 is [M] and nothing in the layout was invocable
    outside the eval harness.

Task fixture schema (one JSON object per line in eval/tasks.jsonl):

  {
    "id":        "fix-import",
    "goal":      "Tests in tests/ fail on an import error. Fix it.",
    "setup":     "scripts/reset.sh fix-import",
    "check":     "cd /workspace && pytest -q",
    "split":     "dev",
    "max_turns": 12,
    "budget":    200000
  }


--------------------------------------------------------------------------------
13. CODE ECONOMY RULES
--------------------------------------------------------------------------------

Binding on the implementing agent. Violations are defects, not style notes.

  CE-01  A separate module requires two callers OR two implementations.
         One of each is not enough.

  CE-02  A framework earns its place at break-even, not before. Count the
         lines it costs against the lines it saves at the CURRENT scale, not
         the anticipated one.

  CE-03  Every state field must be read somewhere. A field only ever written
         is dead weight that survives because it looks purposeful.

  CE-04  Two nodes that never branch apart are one node. See CE-07 for the
         one exception.

  CE-05  No module-level I/O and no module-level client construction. Reading
         a prompt file at import time breaks every test that imports the
         module.

  CE-06  Prefer default overwrite semantics for state. Custom reducers are
         justified only when two nodes write the same field in one superstep.

  CE-07  EXCEPTION TO CE-04: gate and execute must never merge. gate suspends
         on interrupt() and re-executes from its first line on resume. Merged,
         every already-executed tool fires a second time. This separation is
         load-bearing, not stylistic.

Applying CE-01 to CE-06 to the v1 skeleton removes roughly 80 of 205 lines:

  CUT                                    RULE   RATIONALE
  -------------------------------------  -----  ------------------------------
  merge() reducer + __replace__ sentinel  CE-06  nodes return the full list;
                                                 compaction becomes an ordinary
                                                 return
  @tool decorator + inspect machinery     CE-02  ~25 lines + 5/tool against
                                                 ~8/tool hand-written; three
                                                 tools is below break-even
  execute and observe as separate nodes   CE-04  also removes raw_results from
                                                 state
  scratch field                           CE-03  written by observe, read by
                                                 nothing
  plan and cursor fields                  CE-03  no plan node at v1
  INSTALL set in policy.py                CE-02  the sandbox is why pip install
                                                 is harmless; two mechanisms
                                                 guarding one risk
  max_output threaded through state       CE-03  read from the tool dict at the
                                                 use site
  module-level SOUL = open(...).read()    CE-05  load inside the node

V1 STATE SHAPE — no reducers, no Annotated, no operator import

  class AgentState(TypedDict):
      messages: list[dict]      # default overwrite; nodes return full list
      turns: int
      max_turns: int
      spent_tokens: int
      budget_tokens: int
      failures: int             # CONSECUTIVE failed turns, reset on success
      verdict: str | None
      approved: list[dict]
      denied: list[dict]

config.py is new surface area and still nets negative: it collapses the two
workspace-root definitions into one and pulls the magic numbers (compaction
threshold, head/tail line counts, per-tool caps) out of three modules.

shrink() returns a plain string, not a tuple. The spill path belongs inside
the returned text where the model can act on it — that was the only place it
was ever useful.

RECONCILIATION WITH SECTIONS 3 AND 4

  The control-flow diagram in section 3 and the turn lifecycle in section 4.1
  show EXECUTE and OBSERVE as distinct stages. They remain distinct STAGES;
  CE-04 makes them a single NODE in the implementation, because no edge ever
  separates them. Read section 3 as the logical flow and section 13 as the
  code shape. Where the two are compared, section 13 governs.

  Consequently section 3's deterministic node count is four in v1, not five:
  gate, execute (absorbing observe), reflect, finish.

================================================================================
END OF SPECIFICATION
================================================================================
