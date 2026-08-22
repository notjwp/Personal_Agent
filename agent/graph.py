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
import json
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
    budget_tokens: int
    failures: int
    verdict: str | None
    approved: list[dict]
    denied: list[dict]


def new_state(goal: str, max_turns: int | None = None,
              budget_tokens: int | None = None) -> AgentState:
    """A fresh run. Two callers - the CLI and the eval harness - which is what
    earns this a home here rather than being duplicated in both."""
    return {
        "messages": [{"role": "user", "content": goal}],
        "turns": 0,
        "max_turns": max_turns if max_turns is not None else settings.MAX_TURNS,
        "spent_tokens": 0,
        "budget_tokens": budget_tokens if budget_tokens is not None else settings.BUDGET_TOKENS,
        "failures": 0,
        "verdict": None,
        "approved": [],
        "denied": [],
    }


# --------------------------------------------------------------------- nodes

def act(state: AgentState, config: RunnableConfig) -> dict:
    """The only node that touches a model.

    Which provider answers lives in agent/provider.py; this node keeps the trace
    bookkeeping and the state update, and stays ignorant of who replied.
    """
    cfg = config.get("configurable", {})
    system = SOUL.read_text(encoding="utf-8")   # CE-05: read here, not at import

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
    reply = call_model(state["messages"], system, registry.schemas(), cfg.get("on_text"))

    trace = cfg.get("trace")
    if trace is not None:
        trace.append({
            "kind": "model",
            # Recorded per turn: a score is meaningless without knowing which model
            # produced it, and this project expects to switch providers.
            "provider": settings.PROVIDER,
            "billed_tokens": reply.billed_tokens,
            "cache_read_tokens": reply.cache_read_tokens,
            "stop_reason": reply.stop_reason,
        })

    return {
        "messages": state["messages"] + [{"role": "assistant", "content": reply.blocks}],
        "spent_tokens": state["spent_tokens"] + reply.billed_tokens,
    }


def gate(state: AgentState, config: RunnableConfig) -> dict:
    """Classify every call before any side effect.

    NO SIDE EFFECTS. This node suspends on interrupt() and re-executes from its
    first line on resume, rebuilding approved/denied from scratch. No logging, no
    counters, no writes. This is why gate and execute are separate nodes: merged,
    every already-executed tool would fire again on every resume.
    """
    autonomous = config.get("configurable", {}).get("autonomous", True)
    approved, denied = [], []

    for call in _tool_calls(state["messages"][-1]):
        verdict, reason = classify(call["name"], call["input"], autonomous)
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

    return {
        "messages": state["messages"] + [{"role": "user", "content": results}],
        "turns": state["turns"] + 1,
        # correction (c): overwrite semantics, reset to 0 when every result succeeded
        "failures": 0 if failed == 0 else state["failures"] + 1,
    }


def reflect(state: AgentState) -> dict:
    """Deterministic only. Checks run in a fixed order; the first match wins."""
    if state["spent_tokens"] > settings.COMPACT_AT * state["budget_tokens"]:
        return {"verdict": "compact"}                                   # (a)
    if state["turns"] >= state["max_turns"]:
        return {"verdict": "stuck"}                                     # (b)
    if _last_three_signatures_identical(state["messages"]):
        return {"verdict": "stuck"}                                     # (c)
    if state["failures"] >= 3:
        return {"verdict": "replan"}                                    # (d)
    if state["messages"][-1]["role"] == "assistant":                    # (e)
        return {"verdict": "done" if _made_a_call(state["messages"]) else "continue"}
    return {"verdict": "continue"}                                      # (f)


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
    # compact and replan have no node at v1 and terminate here. Their frequency in
    # the baseline is the measurement that earns the compaction and plan layers.
    return "act" if state["verdict"] == "continue" else "finish"


def _build() -> StateGraph:
    b = StateGraph(AgentState)
    b.add_node("act", act)
    b.add_node("gate", gate)
    b.add_node("execute", execute)
    b.add_node("reflect", reflect)
    b.add_node("finish", finish)

    b.add_edge(START, "act")                    # correction (a): not "plan"
    b.add_conditional_edges("act", _route_after_act, ["gate", "reflect"])
    b.add_edge("gate", "execute")               # CE-07: never merged
    b.add_edge("execute", "reflect")
    b.add_conditional_edges("reflect", _route_after_reflect, ["act", "finish"])
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
        _APP = _build().compile(checkpointer=SqliteSaver(conn))
    return _APP
