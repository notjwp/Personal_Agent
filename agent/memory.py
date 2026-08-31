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

**Keyword search only, and now for a measured reason rather than an untested**
**gate.** FR-408 asked for semantic retrieval only on "a measured shortfall in
keyword recall". Both halves were measured (eval/CHANGELOG.md): recall@3 was
2/6, and the shortfall was in the QUERY - `_terms` OR-ed every word, so a goal
matching eight corpus-wide words outranked one matching two rare ones. Fixing
that alone reached 5/6, free.

A dense lane over bge-small was then built and reverted: fused it scored 5/6,
the same score with the same single miss, for 450 MB of image.

CE-05: nothing here runs at import, and the database is created on first use.
NFR-602: every function below is testable with no API key and no network.
"""
from __future__ import annotations

import json
import sqlite3
import time

from agent import config, policy

# Names this module put into policy.RISK, so deactivate() removes exactly
# those and cannot strip a built-in's classification.
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
    USING fts5(goal, answer, files, commands, content='episodes',
               content_rowid='id', tokenize='porter');

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


def _words(text: str) -> list[str]:
    """Alphanumeric words over two characters, lowercased, in order."""
    return [w for w in "".join(c if c.isalnum() else " " for c in text).lower().split()
            if len(w) > 2]


def _document_frequency() -> dict:
    """How many episodes each word appears in. Cheap at this scale, and the
    thing that makes a query discriminate: a word in most episodes carries no
    information about which one is meant."""
    df: dict = {}
    total = 0
    conn = _connect()
    try:
        rows = conn.execute("SELECT goal, answer FROM episodes").fetchall()
    finally:
        conn.close()
    for row in rows:
        total += 1
        for word in set(_words(f'{row["goal"]} {row["answer"] or ""}')):
            df[word] = df.get(word, 0) + 1
    return {"df": df, "n": total}


def _terms(text: str, stats: dict | None = None) -> str:
    """An FTS5 MATCH query from free text, weighted by how rare each word is.

    Measured: OR-ing every word scored recall@3 of 2/6 on a 36-goal corpus,
    because a goal matching eight corpus-wide words - write, file, workspace,
    called - outranked the one matching two rare ones. Keeping only the rarest
    two scored 5/6.

    User text is also full of characters FTS5 treats as syntax, so every word is
    requoted as a literal and OR-ed: a partial overlap returns something.
    """
    words = list(dict.fromkeys(_words(text)))
    if stats and stats["n"]:
        df = stats["df"]
        total = stats["n"]
        # Only words the corpus actually contains can rank anything. Sorting the
        # rest by rarity puts df == 0 FIRST - terms that match nothing - which
        # returned an empty result on any corpus small enough that every real
        # term appears everywhere. Found by the offline suite, not by the
        # 36-goal measurement, where every target word had df >= 1.
        present = [w for w in words if df.get(w, 0) > 0]
        if present:
            # A word in EVERY episode discriminates nothing, but dropping it is
            # only safe while something else survives.
            discriminating = [w for w in present if df[w] < total] or present
            words = sorted(discriminating, key=lambda w: df[w])[:QUERY_TERMS]
    return " OR ".join(f'"{w}"' for w in words[:40])


# How many of the rarest query words survive into the MATCH. Swept once the
# df == 0 defect was fixed: 2 and 3 both score 5/6, 1 and 4+ score 4/6.
QUERY_TERMS = 2


def search(query: str, limit: int | None = None) -> list[dict]:
    """Past episodes matching `query`, most relevant first. Keyword only.

    Stale episodes are DOWN-RANKED, not dropped: past MEMORY_STALE_DAYS a row
    keeps competing at MEMORY_STALE_DECAY of its score. There is no reinforcement
    shield, unlike the design this follows - we record when an episode was
    WRITTEN and never when it was last USED, so there is no signal to shield on.
    Adding one means a new column and a write on every retrieval.

    A dense lane over bge-small embeddings was built, measured and REVERTED. On
    the six recall pairs it scored 5/6 fused and 3/6 alone, against 5/6 for this
    function on its own - the same score with the same single miss, at every
    depth from 5 to 35. It cost 450 MB of image for nothing. The four-channel
    shape it came from scored 1/6. eval/CHANGELOG.md carries the ablation.

    What DID move the number was the query, not the index: see `_terms`.
    """
    terms = _terms(query, _document_frequency())
    if not terms:
        return []
    conn = _connect()
    try:
        rows = conn.execute(
            # MATCHED ON `goal` ALONE, not the whole row. Measured: matching every
            # column scores 4/6 against 5/6, because a query's rare words turn up
            # in some other episode's answer or command list and outrank the
            # episode whose GOAL is the thing being recalled.
            "SELECT e.* FROM episodes_fts f JOIN episodes e ON e.id = f.rowid"
            " WHERE f.goal MATCH ?"
            # Staleness, applied to the SCORE rather than by filtering. bm25 is
            # negative and more negative is better, so multiplying a stale row by
            # a fraction moves it toward zero and down the list. A filter would
            # hide the only episode that answers a question nobody asked recently.
            " ORDER BY bm25(episodes_fts) *"
            "   (CASE WHEN ? - e.at > ? THEN ? ELSE 1.0 END), e.at DESC"
            " LIMIT ?",
            (terms, time.time(), config.MEMORY_STALE_DAYS * 86400,
             config.MEMORY_STALE_DECAY,
             limit if limit is not None else config.MEMORY_EPISODES)).fetchall()
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


def now() -> str:
    """The working scratchpad, or empty when nothing has been recorded."""
    if not config.NOW.exists():
        return ""
    return config.NOW.read_text(encoding="utf-8", errors="replace").strip()


def write_now(goal: str, verdict: str | None, plan: list[str], cursor: int,
              files: list[str]) -> str:
    """Record where this session got to. Overwrites; returns what was written.

    WRITTEN BY A RULE, never requested from the model. Everything here is already
    in state, so this needs no decision and no model call - which is the whole
    reason it will actually happen. The counter-example is in this repo: `learn`
    asked the agent to record something and was called 0 times in 15 sessions,
    while deterministic episode injection went 0/18 to 15/18.

    The unfinished half is the point. A run that ends `stuck` at turn 30 currently
    tells the next session nothing about how far it got, so a resumed or scheduled
    task re-derives it.
    """
    lines = [f"Last session was asked: {' '.join(str(goal).split())[:300]}",
             f"It ended: {verdict or 'no verdict'}"]
    if plan:
        step = plan[cursor] if 0 <= cursor < len(plan) else plan[-1]
        lines.append(f"Reached step {min(cursor + 1, len(plan))} of {len(plan)}:"
                     f" {step}")
        remaining = plan[cursor + 1:]
        if remaining and verdict != "done":
            lines.append("Still to do: " + "; ".join(remaining[:5]))
    if files:
        lines.append("Files touched: " + ", ".join(sorted(files)[:10]))

    body = "# What I was last doing" + chr(10) * 2 + chr(10).join(lines) + chr(10)
    config.NOW.parent.mkdir(parents=True, exist_ok=True)
    config.NOW.write_text(body, encoding="utf-8", newline=chr(10))
    return body


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
    # Ahead of the episodes: what the last session was doing outranks what some
    # earlier session concluded, and the cap below truncates the TAIL.
    current = now()
    if current:
        parts.append(current)
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
