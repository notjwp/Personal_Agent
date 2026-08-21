"""Episodic memory (FR-407) and the durable profile (FR-406).

Both `[S]`, and §9 puts `[S]` out of scope "until the `[M]` set passes evaluation".
It has — Definition of Done 9/9 — so this is scope arriving on schedule, not a
liberty taken.

Two stores, because they answer two different questions:

  episodes   what happened in past sessions, searched by keyword. Written
             automatically at `finish`, from state and trace that already exist.
  AGENT.md   what is durably true about the user, written by the agent through
             the `remember` tool. §12 names the file and says "written by the
             agent"; a tool is how that stays true without `finish` calling a model.

**Keyword search only.** FR-408 gates semantic retrieval on "a measured shortfall
in keyword recall" and §11 forbids vectors until keyword recall is "measured and
found wanting". This module is that measurement's subject, not its conclusion.

CE-05: nothing here runs at import, and the database is created on first use.
NFR-602: every function below is testable with no API key and no network.
"""
from __future__ import annotations

import json
import sqlite3
import time

from agent import config, policy

# Names this module put into policy.RISK, so deactivate() removes exactly those.
# Tracked per-module rather than diffed against a snapshot: mcp.py registers too,
# and a snapshot taken by whichever imported first would silently own the other's
# entries.
_REGISTERED: list[str] = []

SCHEMA = """
CREATE TABLE IF NOT EXISTS episodes (
    id        INTEGER PRIMARY KEY,
    thread_id TEXT NOT NULL,
    at        REAL NOT NULL,
    goal      TEXT NOT NULL,
    verdict   TEXT,
    answer    TEXT,
    files     TEXT,
    commands  TEXT
);
CREATE VIRTUAL TABLE IF NOT EXISTS episodes_fts
    USING fts5(goal, answer, files, commands, content='episodes', content_rowid='id');
"""


def _connect() -> sqlite3.Connection:
    """Open the store, creating it if absent. Never at import (CE-05)."""
    config.MEMORY_DB.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(config.MEMORY_DB))
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def write_episode(thread_id: str, goal: str, verdict: str | None, answer: str,
                  files: list[str], commands: list[str]) -> int:
    """Record one finished session. Returns its id.

    Everything stored here is already in `state["messages"]` and the trace, so this
    needs NO model call — which is what keeps `finish` a deterministic node and
    leaves `act` the only node that touches a model. A model-written summary was
    considered and rejected on exactly that ground.

    §4.3's retention list — decisions made, files touched, commands that worked,
    errors hit, artifact paths — was written for compaction and is the right list
    here too. The goal and the final answer are added because THIS project's memory
    has to answer "what did the user tell me", and the user's words are the goal.
    """
    conn = _connect()
    try:
        with conn:
            cur = conn.execute(
                "INSERT INTO episodes (thread_id, at, goal, verdict, answer, files,"
                " commands) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (thread_id, time.time(), goal, verdict, answer,
                 json.dumps(files), json.dumps(commands)))
            rowid = cur.lastrowid
            # An external-content FTS5 table is not populated by the INSERT above.
            conn.execute(
                "INSERT INTO episodes_fts (rowid, goal, answer, files, commands)"
                " VALUES (?, ?, ?, ?, ?)",
                (rowid, goal, answer, " ".join(files), " ".join(commands)))
        return rowid
    finally:
        conn.close()


def _terms(text: str) -> str:
    """An FTS5 MATCH query from free text, defensively.

    User text is full of characters FTS5 treats as syntax - quotes, hyphens,
    parentheses, `*` - and one of them turns a lookup into a SyntaxError at exactly
    the moment recall was supposed to happen. Every word is requoted as a literal
    and OR-ed, so a partial overlap still returns something rather than nothing.
    """
    words = [w for w in "".join(c if c.isalnum() else " " for c in text).split()
             if len(w) > 2]
    return " OR ".join(f'"{w}"' for w in words[:40])


def search(query: str, limit: int | None = None) -> list[dict]:
    """Past episodes matching `query`, most relevant first. Keyword only."""
    terms = _terms(query)
    if not terms:
        return []
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT e.* FROM episodes_fts f JOIN episodes e ON e.id = f.rowid"
            " WHERE episodes_fts MATCH ? ORDER BY bm25(episodes_fts), e.at DESC"
            " LIMIT ?",
            (terms, limit if limit is not None else config.MEMORY_EPISODES)).fetchall()
        return [dict(r) for r in rows]
    except sqlite3.OperationalError:
        # A malformed MATCH must never take down the run. No memory is a worse
        # session; a crash is a lost one.
        return []
    finally:
        conn.close()


def profile() -> str:
    """The durable profile, or empty when nothing has been recorded."""
    if not config.PROFILE.exists():
        return ""
    return config.PROFILE.read_text(encoding="utf-8", errors="replace").strip()


def remember(note: str) -> str:
    """Append a durable fact about the user to AGENT.md.

    Appended, never rewritten: a rewrite needs judgement about what to drop, and
    that judgement would need a model call. Deduplicated on the exact line so an
    agent told the same thing twice does not grow the file forever.
    """
    note = " ".join(str(note).split())
    if not note:
        raise ValueError("nothing to remember - pass the fact as a short sentence.")
    existing = profile()
    if note in existing.splitlines():
        return f"already recorded: {note}"
    config.PROFILE.parent.mkdir(parents=True, exist_ok=True)
    body = (existing + "\n" if existing else "# What I know about you\n\n") + note + "\n"
    config.PROFILE.write_text(body, encoding="utf-8", newline="\n")
    return f"recorded: {note}"


def context_for(goal: str) -> str:
    """Profile plus relevant past sessions, as text for the system prompt.

    Injected into the SYSTEM PROMPT rather than appended to the message list. The
    difference is not cosmetic: a message appended per turn would add a fresh copy
    every turn and grow quadratically, while the system prompt is one fixed cost per
    request. On a provider returning cache_read_tokens of 0 that fixed cost is still
    paid every turn, which is why it is capped rather than merely bounded.
    """
    if not config.MEMORY_ENABLED:
        return ""
    parts = []
    who = profile()
    if who:
        parts.append(who)
    for row in search(goal):
        commands = json.loads(row["commands"] or "[]")
        line = f'- earlier you were asked: "{row["goal"]}"'
        if row["answer"]:
            line += f'\n  you concluded: {row["answer"]}'
        if commands:
            line += f'\n  commands that worked: {"; ".join(commands[:3])}'
        parts.append(line)
    if not parts:
        return ""
    body = "\n\n".join(parts)[:config.MEMORY_INJECT_CHARS]
    return ("# What you remember\n\n"
            "From earlier sessions with this user. Treat it as established fact and "
            "act on it without asking again.\n\n" + body)


# ------------------------------------------------------------------ the tool

REMEMBER_SCHEMA = {
    "name": "remember",
    "description": (
        "Save a durable fact about the user or how they work, so future sessions "
        "know it without being told again. Use it for standing preferences and "
        "stable facts, not for details of the task in hand."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "note": {"type": "string",
                     "description": "One short sentence, in the third person."},
        },
        "required": ["note"],
    },
}


def activate() -> list[str]:
    """Register the memory tool for this run. A no-op when memory is off."""
    if not config.MEMORY_ENABLED:
        return []
    if "remember" not in _REGISTERED:
        policy.register("remember", "write")
        _REGISTERED.append("remember")
    return list(_REGISTERED)


def deactivate() -> None:
    """Remove exactly what activate() registered. Safe to call unconditionally."""
    while _REGISTERED:
        policy.RISK.pop(_REGISTERED.pop(), None)


def tools() -> dict[str, dict]:
    """The memory tools active for this run. Empty when memory is off."""
    if not config.MEMORY_ENABLED or "remember" not in _REGISTERED:
        return {}
    return {"remember": {"fn": remember, "schema": REMEMBER_SCHEMA, "risk": "write"}}
