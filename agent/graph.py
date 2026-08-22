"""AgentState, the v1 nodes, and the graph wiring.

Only `act` touches the model. `gate`, `execute`, `reflect` and `finish` are ordinary
code, unit-testable without an API key (NFR-602). That ratio is the most important
design property here, so resist moving logic into the prompt.

Corrections mandated by the build spec, all applied below:
  (a) the entry edge is START -> act; there is no plan node at v1
  (b) `done` is gated on whether a tool call was EVER made, not on a plan cursor
  (c) `failures` is a plain int counting CONSECUTIVE failed turns, reset on success
  (d) the risk map in policy.py is the single path to a verdict
  (e) tracing is present now, not deferred
"""
import inspect
import json
import re
import sqlite3
import time
from hashlib import sha256
from pathlib import Path
from typing import TypedDict

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import RunnableConfig, interrupt

from agent import config as settings
from agent import memory, skills
from agent.context import shrink
from agent.policy import classify
from agent.provider import call_model
from agent import registry
from agent.tools import toolset

SOUL = Path(__file__).resolve().parent.parent / "prompts" / "SOUL.md"
PLAN = Path(__file__).resolve().parent.parent / "prompts" / "PLAN.md"

# A numbered or bulleted line from the planner's reply. Deterministic parsing, so
# adopting a plan needs no second model call.
_STEP = re.compile(r"^\s*(?:\d+[.)]|[-*])\s+(.{3,})$")

DENIAL_TEMPLATE = "Denied by policy: {reason}. Find another approach."
SPILL_MARKER = "[full output: "


class AgentState(TypedDict):
    """Exactly nine fields. No reducers, no Annotated, no operator import.

    Nodes return the FULL messages list, so compaction later is an ordinary return
    rather than a custom merge. Mode and thread_id travel in the graph config, not
    here: they are run-level configuration, not evolving state.
    """
    messages: list[dict]
    turns: int
    max_turns: int
    spent_tokens: int
    spent_seconds: float      # WORKING seconds; see config.MAX_SECONDS
    budget_tokens: int
    failures: int
    verdict: str | None
    approved: list[dict]
    denied: list[dict]
    # Planning (FR-101, FR-105). CE-03 cut `plan` and `cursor` from v1 because
    # nothing read them; both are read now, by act and by reflect.
    phase: str                # "planning" until the plan is adopted, then "working"
    plan: list[str]
    cursor: int
    plan_turns: int           # counted apart from `turns`; see config.PLAN_MAX_TURNS


def new_state(goal: str, max_turns: int | None = None,
              budget_tokens: int | None = None) -> AgentState:
    """A fresh run. Two callers - the CLI and the eval harness - which is what
    earns this a home here rather than being duplicated in both."""
    return {
        "messages": [{"role": "user", "content": goal}],
        "turns": 0,
        "max_turns": max_turns if max_turns is not None else settings.MAX_TURNS,
        "spent_tokens": 0,
        "spent_seconds": 0.0,
        "budget_tokens": budget_tokens if budget_tokens is not None else settings.BUDGET_TOKENS,
        "failures": 0,
        "verdict": None,
        "approved": [],
        "denied": [],
        "phase": "planning" if settings.PLAN_ENABLED else "working",
        "plan": [],
        "cursor": 0,
        "plan_turns": 0,
    }


def continue_state(prior: AgentState, message: str) -> dict:
    """A follow-up turn in a thread that already terminated (FR-701, "chat").

    Returns a PARTIAL state on purpose. Nodes overwrite per key, so every field
    left out - spent_tokens, budget_tokens, max_turns - keeps its checkpointed
    value, and that omission is the safety property: spend stays CUMULATIVE, so
    the budget still binds across a long conversation.

    The turn counter is the one thing that must reset. reflect (b) fires on
    `turns >= max_turns`, so a second message into a thread that used its whole
    allowance would return `stuck` before the model was called even once.

    Lives here rather than in the interface layer because the nine fields are
    this module's business - the same reason new_state() is here.
    """
    return {
        "messages": prior["messages"] + [{"role": "user", "content": message}],
        "turns": 0,
        "failures": 0,
        "verdict": None,
        # A follow-up is a new goal, so it is planned like one (UR-02). The old
        # plan is dropped rather than resumed: its steps described the previous
        # request and would be injected into every turn of this one.
        "phase": "planning" if settings.PLAN_ENABLED else "working",
        "plan": [],
        "cursor": 0,
        "plan_turns": 0,
    }


# --------------------------------------------------------------------- nodes

def act(state: AgentState, config: RunnableConfig) -> dict:
    """The only node that touches a model.

    Which provider answers lives in agent/provider.py; this node keeps the trace
    bookkeeping and the state update, and stays ignorant of who replied.
    """
    cfg = config.get("configurable", {})
    system = SOUL.read_text(encoding="utf-8")   # CE-05: read here, not at import

    # Section 4.1 step 3 is explicit that this goes in the SYSTEM PROMPT and not
    # into the message list, which "pollutes history and creates gaps" - the same
    # placement memory and skills already use.
    if state.get("phase") == "planning":
        system = f"{system}\n\n{PLAN.read_text(encoding='utf-8')}"
        if state.get("plan_turns", 0) >= settings.PLAN_MAX_TURNS:
            # Out of research turns. ASK for the plan rather than going straight
            # to adopt: measured, not anticipated - the first version treated the
            # cap as an exit, so the last message was a tool result, there was no
            # plan text to parse, and the goal-as-one-step fallback fired on all
            # three runs. The mechanism has to produce a plan before a pass rate
            # can say anything about it.
            system = (f"{system}\n\n## No research turns left\n\n"
                      f"You have used all {settings.PLAN_MAX_TURNS} research turns. "
                      f"Write the plan NOW, as numbered steps, and call no tools.")
    elif state.get("plan"):
        plan, cursor = state["plan"], min(state.get("cursor", 0), len(state["plan"]) - 1)
        # The WHOLE plan, not just the current step. It costs about 70 tokens a
        # turn on a provider that caches nothing, and buys the model sight of
        # what it already did and what is still coming - without which step 3
        # arrives with no idea that step 4 is "run the suite".
        listing = "\n".join(
            f"{'->' if i == cursor else '  '} {i + 1}. {step}"
            for i, step in enumerate(plan))
        system = (f"{system}\n\n## Your plan\n\n{listing}\n\n"
                  f"You are on step {cursor + 1} of {len(plan)}. Do that step.")
        trace = cfg.get("trace")
        if trace is not None:
            trace.append({"kind": "step", "cursor": cursor, "of": len(plan),
                          "text": plan[cursor]})

    # Retrieved memory goes in the SYSTEM PROMPT, not the message list. A message
    # appended per turn would add a fresh copy each turn and grow quadratically;
    # the system prompt is one fixed cost per request. It is still charged EVERY
    # request on a provider that caches nothing, which is why context_for() caps it.
    recalled = memory.context_for(_goal(state["messages"]))
    if recalled:
        system = f"{system}\n\n{recalled}"
        trace = cfg.get("trace")
        if trace is not None:
            trace.append({"kind": "memory", "chars": len(recalled)})

    # Level 1 of progressive disclosure: names and descriptions only. The bodies
    # cost nothing until the agent calls load_skill, which is the whole point on a
    # provider that re-sends and re-charges the prompt every single turn.
    catalogue = skills.index()
    if catalogue:
        system = f"{system}\n\n{catalogue}"
        trace = cfg.get("trace")
        if trace is not None:
            trace.append({"kind": "skills", "chars": len(catalogue)})

    # Rebuilt per turn rather than bound at import: which tools exist depends on
    # what activated for THIS run, and CE-05 forbids deciding that at import.
    # The final planning turn goes out with NO TOOL SCHEMAS. Measured twice: told
    # in the system prompt that it had no research turns left and must call no
    # tools, the model called one anyway - on all three runs - so the plan was
    # never written and `adopt` fell back to the goal every time. An instruction
    # the model can ignore becomes an absence it cannot: it will not call what it
    # cannot see. This is what makes FR-101 true rather than nominally present.
    tools = ([] if state.get("phase") == "planning"
             and state.get("plan_turns", 0) >= settings.PLAN_MAX_TURNS
             else registry.schemas())
    started = time.monotonic()
    reply = call_model(state["messages"], system, tools, cfg.get("on_text"))
    elapsed = time.monotonic() - started

    trace = cfg.get("trace")
    if trace is not None:
        trace.append({
            "kind": "model",
            # Recorded per turn: a score is meaningless without knowing which model
            # produced it, and this project expects to switch providers.
            "provider": settings.PROVIDER,
            "billed_tokens": reply.billed_tokens,
            # NFR-102 excludes model time from framework cost, so the exclusion
            # has to be on the record rather than estimated.
            "ms": round(elapsed * 1000, 3),
            "cache_read_tokens": reply.cache_read_tokens,
            "stop_reason": reply.stop_reason,
        })

    return {
        "messages": state["messages"] + [{"role": "assistant", "content": reply.blocks}],
        "spent_tokens": state["spent_tokens"] + reply.billed_tokens,
        # NFR-304. Accumulated by the two nodes that spend real time - this one
        # waiting on the provider, execute waiting on tools - rather than read
        # off the clock, so a thread resumed tomorrow is not instantly over its
        # cap because the calendar moved.
        "spent_seconds": state.get("spent_seconds", 0.0) + elapsed,
    }


def gate(state: AgentState, config: RunnableConfig) -> dict:
    """Classify every call before any side effect.

    NO SIDE EFFECTS. This node suspends on interrupt() and re-executes from its
    first line on resume, rebuilding approved/denied from scratch. No logging, no
    counters, no writes. This is why gate and execute are separate nodes: merged,
    every already-executed tool would fire again on every resume.
    """
    autonomous = config.get("configurable", {}).get("autonomous", True)
    # While planning the agent may look but not touch (UR-02). Enforced in the
    # gate rather than asked for in the prompt, because an approval shown after
    # the files have already changed is theatre.
    planning = state.get("phase") == "planning"
    approved, denied = [], []

    for call in _tool_calls(state["messages"][-1]):
        verdict, reason = classify(call["name"], call["input"], autonomous, planning)
        if verdict == "auto":
            approved.append(call)
        elif verdict == "deny":
            denied.append({**call, "reason": reason})
        else:
            decision = interrupt({"call": call, "reason": reason})
            if decision == "allow":
                approved.append(call)
            else:
                denied.append({**call, "reason": "rejected by user"})

    return {"approved": approved, "denied": denied}


def execute(state: AgentState, config: RunnableConfig) -> dict:
    """Run approved calls, then observe. CE-04 merges the two stages into one node
    because no edge ever separates them."""
    trace = config.get("configurable", {}).get("trace")
    results, failed = [], 0
    node_started = time.monotonic()

    for call in state["approved"]:
        started = time.monotonic()
        try:
            raw = str(toolset()[call["name"]]["fn"](**call["input"]))
            is_error = False
        except Exception as exc:                    # FR-208: never propagates out
            raw = f"{type(exc).__name__}: {exc}"
            is_error = True
        if is_error:
            failed += 1
        body = shrink(call["name"], raw)            # FR-401/402
        results.append({"type": "tool_result", "tool_use_id": call["id"],
                        "content": body, "is_error": is_error})
        _log(trace, call, "auto", time.monotonic() - started, raw, body, is_error)

    for call in state["denied"]:
        results.append({"type": "tool_result", "tool_use_id": call["id"],
                        "content": DENIAL_TEMPLATE.format(reason=call["reason"]),
                        "is_error": True})
        _log(trace, call, "deny", 0.0, "", "", True)  # a denial is still a tool call
        failed += 1

    # Research turns are counted SEPARATELY and are not charged against max_turns.
    # This is what makes planning affordable: MAX_TURNS is 12, and a shared
    # counter would let four turns of reading starve the very cases planning
    # exists to help.
    planning = state.get("phase") == "planning"
    return {
        "messages": state["messages"] + [{"role": "user", "content": results}],
        "turns": state["turns"] + (0 if planning else 1),
        "plan_turns": state.get("plan_turns", 0) + (1 if planning else 0),
        "spent_seconds": state.get("spent_seconds", 0.0)
        + (time.monotonic() - node_started),
        # correction (c): overwrite semantics, reset to 0 when every result succeeded
        "failures": 0 if failed == 0 else state["failures"] + 1,
    }


def reflect(state: AgentState) -> dict:
    """Deterministic only. Checks run in a fixed order; the first match wins."""
    if state["spent_tokens"] > settings.COMPACT_AT * state["budget_tokens"]:
        return {"verdict": "compact"}                                   # (a)
    # NFR-304's third cap, checked before the phase branch so it binds during
    # research as well as work. Terminates as `stuck` rather than as a fifth
    # verdict, deliberately: FR-104 already names exactly four terminal outcomes
    # and running out of time is a way of being stuck, not a new kind of ending.
    if state.get("spent_seconds", 0.0) >= settings.MAX_SECONDS:
        return {"verdict": "stuck"}                                     # (a2)

    if state.get("phase") == "planning":
        # A reply carrying no tool call IS the plan - the planner saying it has
        # finished looking. Running out of research turns produces one too:
        # a planner that will not stop reading must still yield something rather
        # than spend the whole run on reconnaissance.
        if state["messages"][-1]["role"] == "assistant":
            return {"verdict": "planned"}
        # Strictly GREATER than the cap, so reaching it buys one more act turn -
        # the one act spends demanding the plan. This is the hard stop for a
        # planner that ignores that demand and keeps calling tools.
        if state.get("plan_turns", 0) > settings.PLAN_MAX_TURNS:
            return {"verdict": "planned"}
        return {"verdict": "continue"}

    if state["turns"] >= state["max_turns"]:
        return {"verdict": "stuck"}                                     # (b)
    if _last_three_signatures_identical(state["messages"]):
        return {"verdict": "stuck"}                                     # (c)
    if state["failures"] >= 3:
        return {"verdict": "replan"}                                    # (d)
    if state["messages"][-1]["role"] == "assistant":                    # (e)
        # Section 9 step 2 (b) said to restore the cursor check "only when the
        # plan node is added". This is that moment - and the made-a-call guard
        # stays, because with AGENT_PLAN=off `plan` is [] and that guard is the
        # only thing standing between a first text-only reply and a false `done`.
        if not _made_a_call(state["messages"]):
            return {"verdict": "continue"}
        plan = state.get("plan") or []
        if state.get("cursor", 0) + 1 < len(plan):
            return {"verdict": "continue", "cursor": state["cursor"] + 1}
        return {"verdict": "done"}
    return {"verdict": "continue"}                                      # (f)


def adopt(state: AgentState, config: RunnableConfig) -> dict:
    """Capture the plan, and interactively put it in front of a human (UR-02, UR-05).

    NO SIDE EFFECTS. This node suspends on interrupt() and re-executes from its
    first line on resume - CE-07's rule, which applies to EVERY node upstream of
    a suspension point and not only to gate. It parses and it asks. It does not
    log, count, or write.

    Deterministic: the steps are parsed out of text the model already produced, so
    this adds no model call and the ratio section 3 calls the system's most
    important design property is untouched. Section 3 draws PLAN as a node that
    calls a model; CE-04 says two nodes that never branch apart are one node, and
    planning differs from working only in the prompt, the gate and reflect's exit.
    Section 13 governs the code shape where the two disagree.
    """
    steps = _steps(_final_text(state["messages"])) or [_goal(state["messages"])]

    if config.get("configurable", {}).get("autonomous", True):
        return {"phase": "working", "plan": steps, "cursor": 0}

    decision = interrupt({"plan": steps, "reason": "plan ready for review"})
    if decision == "accept":
        return {"phase": "working", "plan": steps, "cursor": 0}

    # Anything else revises. The note goes back as an ordinary user message and
    # the phase stays `planning`, so the loop re-enters act and plans again -
    # which is also why a dismissed modal must resolve to revise, not accept.
    note = decision if isinstance(decision, str) and decision not in ("revise", "") \
        else "Revise the plan."
    return {"messages": state["messages"] + [{"role": "user", "content": note}],
            "phase": "planning", "plan": [], "cursor": 0}


def _steps(text: str) -> list[str]:
    """Numbered or bulleted lines from the planner's reply, in order.

    Truncated at PLAN_MAX_STEPS rather than refused: a planner that
    over-decomposes must not fail the run. `adopt` supplies the goal itself when
    this returns nothing, for the same reason.
    """
    found = [match.group(1).strip()
             for match in (_STEP.match(line) for line in text.splitlines())
             if match]
    return found[:settings.PLAN_MAX_STEPS]


def finish(state: AgentState, config: RunnableConfig) -> dict:
    """Terminal. Records the outcome, and writes the episode memory will recall.

    Still a DETERMINISTIC node: everything written is already in `messages`, so no
    model call is needed. A model-written summary was considered and rejected on
    exactly that ground - it would make a fourth node that touches a model, and the
    ratio of deterministic to model-driven nodes is the most important design
    property in this system.
    """
    cfg = config.get("configurable", {})
    trace = cfg.get("trace")
    if trace is not None:
        trace.append({"kind": "terminal", "verdict": state["verdict"],
                      "turns": state["turns"], "spent_tokens": state["spent_tokens"]})

    if settings.MEMORY_ENABLED:
        outcomes = _outcomes(state["messages"])
        memory.write_episode(
            thread_id=str(cfg.get("thread_id", "")),
            goal=_goal(state["messages"]),
            verdict=state["verdict"],
            answer=_final_text(state["messages"]),
            # §4.3's list: files touched and commands that WORKED. Failed calls are
            # excluded deliberately - recalling a command that did not work is worse
            # than recalling nothing, because it reads as advice.
            files=sorted({c["input"]["path"] for c, ok in outcomes
                          if ok and isinstance(c["input"].get("path"), str)}),
            commands=[c["input"]["command"] for c, ok in outcomes
                      if ok and c["name"] == "run_shell"
                      and isinstance(c["input"].get("command"), str)])

    # Phase O-redux. Knowledge is retained WITHOUT the agent electing to retain it:
    # Phase O measured `learn` called 0 times in 15 sessions, with the tool exposed
    # and the prompt asking for it. Everything needed is already in `messages`, so
    # this adds no model call and `finish` stays deterministic.
    for name in skills.extract(state["messages"], _goal(state["messages"])):
        if trace is not None:
            trace.append({"kind": "skill", "name": name})
    return {}


# ------------------------------------------------------------------- helpers

def _tool_calls(message: dict) -> list[dict]:
    """tool_use blocks of an assistant message, normalised to {id, name, input}."""
    if message.get("role") != "assistant" or not isinstance(message.get("content"), list):
        return []
    return [{"id": b["id"], "name": b["name"], "input": b.get("input", {})}
            for b in message["content"]
            if isinstance(b, dict) and b.get("type") == "tool_use"]


def _goal(messages: list[dict]) -> str:
    """The user's opening message - which for this agent IS what the user said."""
    first = messages[0].get("content") if messages else ""
    return first if isinstance(first, str) else json.dumps(first, default=str)


def _final_text(messages: list[dict]) -> str:
    """The last thing the agent said in words, not tool calls."""
    for message in reversed(messages):
        if message.get("role") != "assistant":
            continue
        content = message.get("content")
        if isinstance(content, str):
            return content.strip()
        texts = [b.get("text", "") for b in content or []
                 if isinstance(b, dict) and b.get("type") == "text"]
        joined = "\n".join(t for t in texts if t).strip()
        if joined:
            return joined
    return ""


def _outcomes(messages: list[dict]) -> list[tuple[dict, bool]]:
    """Every tool call paired with whether its result succeeded.

    Derived from `messages` rather than the trace: the trace is optional - the CLI
    may not pass one - and memory must not silently record less depending on who
    invoked the graph.
    """
    errors = {b["tool_use_id"]: bool(b.get("is_error"))
              for m in messages if isinstance(m.get("content"), list)
              for b in m["content"]
              if isinstance(b, dict) and b.get("type") == "tool_result"}
    return [(call, not errors.get(call["id"], False))
            for m in messages for call in _tool_calls(m)]


def _made_a_call(messages: list[dict]) -> bool:
    """Correction (b), the termination guard. With no plan node a cursor check
    evaluates 1 >= 0 and returns `done` on the first text-only reply - including
    "Let me look at the test file first." Restore the cursor check only when the
    plan node lands.
    """
    return any(_tool_calls(m) for m in messages if m.get("role") == "assistant")


def _signature(call: dict) -> str:
    """Stable identity: tool name plus a hash of canonically ordered arguments."""
    blob = json.dumps(call["input"], sort_keys=True, default=str).encode("utf-8")
    return f"{call['name']}:{sha256(blob).hexdigest()[:12]}"


def _last_three_signatures_identical(messages: list[dict]) -> bool:
    turns = [[_signature(c) for c in _tool_calls(m)]
             for m in messages if m.get("role") == "assistant"]
    turns = [t for t in turns if t]
    return len(turns) >= 3 and turns[-1] == turns[-2] == turns[-3]


def _summarise(args: dict) -> str:
    """The most identifying argument, truncated.

    A bounded summary, not the arguments themselves: write_file carries whole file
    contents, and putting those in every trace event would bloat the traces the
    tuning phase has to read. Serves the CLI's live display and the "edited a file
    it never read" check equally.
    """
    for key in ("name", "command", "path", "file"):
        if key in args:
            return str(args[key])[:120]
    return json.dumps(args, default=str)[:120]


def _log(trace, call, verdict, duration, raw, shrunk, is_error) -> None:
    """One structured record per tool call: name, argument hash, verdict, duration,
    byte counts, spill path. Lives in execute, never in gate - logging there would
    fire twice on resume.
    """
    if trace is None:
        return
    spill = ""
    if SPILL_MARKER in shrunk:
        spill = shrunk.split(SPILL_MARKER, 1)[1].split("]", 1)[0]
    trace.append({
        "kind": "tool",
        "tool": call["name"],
        "summary": _summarise(call["input"]),
        "argument_hash": _signature(call).split(":", 1)[1],
        "verdict": verdict,
        # WHY it was refused, not just that it was. The trace has always said a
        # call was denied and never on what grounds, which made a policy refusal
        # indistinguishable from a path escape when reading a run back.
        "reason": call.get("reason", ""),
        "duration_ms": round(duration * 1000),
        "input_bytes": len(json.dumps(call["input"], default=str)),
        "output_bytes": len(raw),
        "shrunk_bytes": len(shrunk),
        "spill_path": spill,
        "is_error": is_error,
    })


# ------------------------------------------------------------------- routing

def _route_after_act(state: AgentState) -> str:
    """tool_use blocks -> gate; text only -> reflect (step complete, NOT
    termination - reflect decides that)."""
    return "gate" if _tool_calls(state["messages"][-1]) else "reflect"


def _route_after_reflect(state: AgentState) -> str:
    # compact and replan still have no node and terminate here. `replan` fired
    # ONCE in 712 recorded rows, which is why the plan layer was not built on it.
    if state["verdict"] == "planned":
        return "adopt"
    return "act" if state["verdict"] == "continue" else "finish"


def _timed(name: str, fn):
    """Wrap a node so it reports its own wall time (NFR-102).

    Applied HERE rather than inside each node, because _build() is the only
    function that knows all six and a node should not carry stopwatch code. The
    nodes themselves are untouched.

    NFR-102 bounds "framework cost per loop iteration, EXCLUDING model and tool
    time". Both exclusions are already on the trace - `act` records the model's
    ms and `execute` records each call's duration_ms - so the subtraction is
    arithmetic over what this adds, not a second measurement.
    """
    takes_config = len(inspect.signature(fn).parameters) > 1

    def node(state, config=None):
        started = time.monotonic()
        out = fn(state, config) if takes_config else fn(state)
        trace = (config or {}).get("configurable", {}).get("trace")
        if trace is not None:
            trace.append({"kind": "node", "node": name,
                          "ms": round((time.monotonic() - started) * 1000, 3)})
        return out

    return node


class _TimedSaver(SqliteSaver):
    """SqliteSaver that reports how long each checkpoint write took (NFR-103).

    The write is timed where it happens rather than around invoke(), because
    NFR-103 bounds the WRITE and a node's wall time already includes everything
    else. `put()` receives the RunnableConfig, so the measurement lands on the
    trace of the run that caused it without any plumbing.

    The only NFR of the three that reaches into a dependency's surface, which is
    why a test pins that the subclass still round-trips state.
    """

    def put(self, config, checkpoint, metadata, new_versions):
        started = time.monotonic()
        try:
            return super().put(config, checkpoint, metadata, new_versions)
        finally:
            trace = (config or {}).get("configurable", {}).get("trace")
            if trace is not None:
                trace.append({"kind": "checkpoint",
                              "ms": round((time.monotonic() - started) * 1000, 3)})


def _build() -> StateGraph:
    b = StateGraph(AgentState)
    b.add_node("act", _timed("act", act))
    b.add_node("gate", _timed("gate", gate))
    b.add_node("execute", _timed("execute", execute))
    b.add_node("reflect", _timed("reflect", reflect))
    b.add_node("adopt", _timed("adopt", adopt))
    b.add_node("finish", _timed("finish", finish))

    b.add_edge(START, "act")                    # correction (a): not "plan"
    b.add_conditional_edges("act", _route_after_act, ["gate", "reflect"])
    b.add_edge("gate", "execute")               # CE-07: never merged
    b.add_edge("execute", "reflect")
    b.add_conditional_edges("reflect", _route_after_reflect,
                            ["act", "adopt", "finish"])
    b.add_edge("adopt", "act")
    b.add_edge("finish", END)
    return b


_APP = None


def get_app():
    """Build the compiled graph.

    A factory rather than a module-level `app`: SqliteSaver needs a live connection,
    and `from_conn_string` is a context manager that would close it. Opening the
    database at import time would also be module-level I/O (CE-05) - it would create
    state.db merely by importing this module, including from every test.
    """
    global _APP
    if _APP is None:
        settings.STATE_DB.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(settings.STATE_DB), check_same_thread=False)
        _APP = _build().compile(checkpointer=_TimedSaver(conn))
    return _APP
