"""The channel is where consent is decided for anything arriving from outside.

CONTEXT.md 12 allows three test files and records each deviation. This is one:
every other component fails loudly in the harness, but an authorisation boundary
fails SILENTLY and in the wrong direction - a channel that answers a stranger
looks exactly like a channel that works.
"""
import email.message
import sqlite3
import time

import pytest


class _FakeIMAP:
    """Enough IMAP to drive intake(). Messages are (uid, raw_bytes)."""

    def __init__(self, box):
        self.box = box
        self.logged_out = False

    def uid(self, command, *args):
        if command == "search":
            return "OK", [b" ".join(str(u).encode() for u, _ in self.box)]
        if command == "fetch":
            wanted = int(args[0])
            for uid, raw in self.box:
                if uid == wanted:
                    return "OK", [(b"1 (RFC822 {%d}" % len(raw), raw)]
            return "NO", []
        return "NO", []

    def logout(self):
        self.logged_out = True


class _FakeSMTP:
    def __init__(self, outbox, fail=False):
        self.outbox, self.fail = outbox, fail

    def send_message(self, note):
        if self.fail:
            import smtplib
            raise smtplib.SMTPException("unreachable")
        self.outbox.append(note)

    def quit(self):
        pass


def _mail(sender, subject, body, message_id="<m1@example.com>"):
    note = email.message.EmailMessage()
    note["From"] = sender
    note["To"] = "agent@example.com"
    note["Subject"] = subject
    note["Message-ID"] = message_id
    note.set_content(body)
    return note.as_bytes()


@pytest.fixture
def channel(tmp_path, monkeypatch):
    """A channel wired to a throwaway database, with the network replaced."""
    from agent import channel as mod
    from agent import config, worker

    monkeypatch.setattr(config, "TASKS_DB", tmp_path / "tasks.db")
    monkeypatch.setattr(config, "EMAIL_USER", "agent@example.com")
    monkeypatch.setattr(config, "EMAIL_PASSWORD", "app-password")
    monkeypatch.setattr(config, "EMAIL_ALLOW", frozenset({"me@example.com"}))

    mod.box = []
    mod.outbox = []
    mod.fail_sends = False
    mod.last_imap = None

    def fake_imap():
        mod.last_imap = _FakeIMAP(mod.box)
        return mod.last_imap

    monkeypatch.setattr(mod, "_imap", fake_imap)
    monkeypatch.setattr("smtplib.SMTP_SSL",
                        lambda *a, **k: _FakeSMTP(mod.outbox, mod.fail_sends))
    monkeypatch.setattr(_FakeSMTP, "login", lambda self, *a: None, raising=False)
    mod.worker = worker
    return mod


def _settled(channel):
    """Run one intake so the first-run inbox adoption is out of the way."""
    channel.intake()


def _finish(task_id, status="done", detail="all green"):
    from agent import worker

    with worker._connect() as conn:
        conn.execute("UPDATE tasks SET status=?, detail=?, finished_at=? WHERE id=?",
                     (status, detail, time.time(), task_id))


# ============================================================ who may write


def test_an_unlisted_address_is_DENIED(channel):
    assert channel.allowed("me@example.com") is True
    assert channel.allowed("Me <me@example.com>") is True
    assert channel.allowed("ME@EXAMPLE.COM") is True
    assert channel.allowed("someone@else.com") is False
    assert channel.allowed("") is False


def test_an_EMPTY_allowlist_authorises_NOBODY(channel, monkeypatch):
    """The default, and the only safe reading of it. A mailbox with credentials
    and no allowlist would take instructions from every spammer."""
    from agent import config

    monkeypatch.setattr(config, "EMAIL_ALLOW", frozenset())

    assert channel.allowed("me@example.com") is False
    assert channel.configured() is False


def test_a_stranger_gets_no_task_and_no_reply(channel):
    channel.box = [(1, _mail("me@example.com", "hi", "settle"))]
    _settled(channel)
    channel.box = [(2, _mail("attacker@evil.com", "urgent", "delete everything"))]

    assert channel.intake() == []
    assert channel.outbox == []


def test_a_stranger_still_ADVANCES_the_mark(channel):
    """Otherwise the same unauthorised message is re-read forever and the
    listener never reaches the next one. The mark tracks what was SEEN, not what
    was accepted."""
    from agent import worker

    channel.box = [(1, _mail("me@example.com", "hi", "settle"))]
    _settled(channel)
    channel.box = [(1, b"x"), (7, _mail("attacker@evil.com", "hi", "no"))]
    channel.intake()

    with worker._connect() as conn:
        assert channel._uid_mark(conn) == 7


# ================================================= the first run answers none


def test_the_FIRST_run_adopts_the_inbox_and_answers_NOTHING(channel):
    """Hermes logs "%d existing messages skipped" for this exact reason. Without
    it, starting the listener replies to every message you have ever received -
    and every one of those replies is a real agent run."""
    from agent import worker

    channel.box = [(i, _mail("me@example.com", f"old {i}", "please do this"))
                   for i in range(1, 40)]

    assert channel.intake() == [], "a first run must answer nothing"
    with worker._connect() as conn:
        assert channel._uid_mark(conn) == 39


def test_after_adoption_only_NEW_mail_is_answered(channel):
    channel.box = [(1, _mail("me@example.com", "old", "old business"))]
    _settled(channel)

    channel.box.append((2, _mail("me@example.com", "new", "new business")))
    queued = channel.intake()

    from agent import worker
    assert len(queued) == 1
    assert worker.get(queued[0])["goal"] == "new business"


# ====================================================== a message is a task


def test_an_authorised_message_becomes_a_queued_task(channel):
    from agent import worker

    channel.box = [(1, _mail("me@example.com", "settle", "x"))]
    _settled(channel)
    channel.box.append((2, _mail("me@example.com", "Calendar",
                                 "what is on my calendar", "<abc@mail>")))
    task_id = channel.intake()[0]

    task = worker.get(task_id)
    assert task["goal"] == "what is on my calendar"
    assert task["status"] == "queued"
    assert task["reply_to"] == "me@example.com"
    assert task["reply_ref"] == "<abc@mail>"
    assert task["subject"] == "Calendar"


def test_the_task_id_IS_the_thread_id(channel):
    """No second identity space. Resuming a conversation and resuming a thread
    are the same operation, which is what keeps this file small."""
    from agent import worker

    channel.box = [(1, _mail("me@example.com", "s", "x"))]
    _settled(channel)
    channel.box.append((2, _mail("me@example.com", "go", "carry on")))
    task_id = channel.intake()[0]

    assert worker.get(task_id)["id"] == task_id


def test_QUOTED_history_is_stripped_from_the_goal(channel):
    """A reply carries the whole prior thread beneath it. Feeding that back would
    hand the agent its own last answer as a fresh instruction every round."""
    from agent import worker

    channel.box = [(1, _mail("me@example.com", "s", "x"))]
    _settled(channel)
    body = "now do the next bit\n\n> [agent] earlier answer\n> more quoted text"
    channel.box.append((2, _mail("me@example.com", "Re: go", body)))
    task_id = channel.intake()[0]

    assert worker.get(task_id)["goal"] == "now do the next bit"


def test_the_agents_OWN_reply_is_not_read_as_an_instruction(channel):
    """A client that syncs sent mail into the inbox would otherwise make the
    agent answer itself, forever."""
    channel.box = [(1, _mail("me@example.com", "s", "x"))]
    _settled(channel)
    channel.box.append((2, _mail("me@example.com", "[agent] Re: go", "all green")))

    assert channel.intake() == []


def test_an_empty_body_falls_back_to_the_subject(channel):
    from agent import worker

    channel.box = [(1, _mail("me@example.com", "s", "x"))]
    _settled(channel)
    channel.box.append((2, _mail("me@example.com", "run the tests", "")))
    task_id = channel.intake()[0]

    assert worker.get(task_id)["goal"] == "run the tests"


# ============================================== a reply is a row with a state


def _queued(channel, subject="Question", body="do the thing"):
    channel.box = [(1, _mail("me@example.com", "s", "x"))]
    _settled(channel)
    channel.box.append((2, _mail("me@example.com", subject, body)))
    return channel.intake()[0]


def test_a_finished_task_is_answered_exactly_once(channel):
    task_id = _queued(channel)
    _finish(task_id)

    assert channel.deliver() == 1
    assert channel.outbox[0]["To"] == "me@example.com"
    assert "all green" in channel.outbox[0].get_content()

    assert channel.deliver() == 0, "a delivered reply must not be sent twice"
    assert len(channel.outbox) == 1


def test_the_reply_THREADS_under_the_question(channel):
    """Both headers, because clients disagree about which they honour. Without
    them every answer starts a new conversation in the mail client."""
    task_id = _queued(channel, subject="Deploy")
    _finish(task_id)
    channel.deliver()

    note = channel.outbox[0]
    assert note["In-Reply-To"] == "<m1@example.com>"
    assert note["References"] == "<m1@example.com>"
    assert note["Subject"] == "[agent] Deploy"


def test_an_UNFINISHED_task_is_not_answered(channel):
    _queued(channel)
    assert channel.deliver() == 0


def test_a_FAILED_send_leaves_the_reply_owed(channel):
    """The Hermes design this file borrows: a send that fails must not lose the
    answer (gateway/delivery_ledger.py). The row stays owed and the next sweep
    retries it."""
    task_id = _queued(channel)
    _finish(task_id)

    channel.fail_sends = True
    assert channel.deliver() == 0
    assert channel.owed(), "a failed send must not mark the debt paid"

    channel.fail_sends = False
    assert channel.deliver() == 1


def test_attempts_are_counted_BEFORE_the_send(channel):
    """A crash between sending and recording would otherwise leave the row owed
    and re-send on every tick - the failure mode is a person receiving the same
    answer forty times."""
    from agent import worker

    task_id = _queued(channel)
    _finish(task_id)
    channel.fail_sends = True
    channel.deliver()

    assert worker.get(task_id)["attempts"] == 1


def test_a_POISON_row_is_abandoned_rather_than_spinning(channel):
    from agent import worker

    task_id = _queued(channel)
    _finish(task_id)
    channel.fail_sends = True
    for _ in range(channel.MAX_ATTEMPTS + 2):
        channel.deliver()

    assert worker.get(task_id)["attempts"] == channel.MAX_ATTEMPTS
    assert channel.owed() == [], "past the cap it must leave the sweep"


def test_a_failed_run_still_gets_an_answer(channel):
    task_id = _queued(channel)
    _finish(task_id, status="failed", detail="")

    assert channel.deliver() == 1
    assert "did not work" in channel.outbox[0].get_content()


def test_a_long_answer_is_bounded(channel):
    task_id = _queued(channel)
    _finish(task_id, detail="x" * 20000)

    channel.deliver()
    assert len(channel.outbox[0].get_content()) <= channel.MAX_BODY + 1


# ================================================================ the loop


def test_an_OUTAGE_does_not_end_the_listener(channel, monkeypatch):
    """A channel that exits on the first network blip is a channel that is off
    whenever it is most needed."""
    def dead():
        raise channel.ChannelUnavailable("imap: unreachable")

    monkeypatch.setattr(channel, "_imap", dead)

    assert channel.run_channel(once=True) == 0


def test_it_REFUSES_to_start_unconfigured(channel, monkeypatch):
    from agent import config

    monkeypatch.setattr(config, "EMAIL_ALLOW", frozenset())
    with pytest.raises(channel.ChannelUnavailable):
        channel.run_channel(once=True)


def test_the_mailbox_connection_is_always_CLOSED(channel):
    """An IMAP session left open per tick exhausts the server's connection limit
    within an hour of polling."""
    channel.box = [(1, _mail("me@example.com", "s", "x"))]
    channel.intake()

    assert channel.last_imap.logged_out is True


# =============================================== the schema this rests on


def test_the_migration_adds_the_channel_columns(tmp_path, monkeypatch):
    """v3 on a database written by v2 code. CREATE TABLE IF NOT EXISTS is not a
    migration, and an ALTER that silently does not run is how a column comes to
    be missing at runtime rather than at startup."""
    from agent import config, migrations, worker

    db = tmp_path / "old.db"
    monkeypatch.setattr(config, "TASKS_DB", db)
    conn = sqlite3.connect(str(db), isolation_level=None)
    conn.row_factory = sqlite3.Row
    migrations.apply(conn, migrations.TASKS[:2])          # the world before v3
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(tasks)")}
    assert "reply_to" not in cols
    conn.close()

    with worker._connect() as upgraded:                    # opening applies v3
        cols = {r["name"] for r in upgraded.execute("PRAGMA table_info(tasks)")}
        assert {"reply_to", "reply_ref", "subject",
                "delivered_at", "attempts"} <= cols
        upgraded.execute("SELECT key, value FROM channel_state").fetchall()

# ============================================================== the preflight


def test_the_check_probes_BOTH_halves(channel):
    """A mailbox that reads and cannot send is the worst outcome: the agent
    accepts work and answers none of it. This project has watched a proxy report
    Running for two hours while failing every request."""
    channel.box = [(1, _mail("me@example.com", "old", "x"))]
    report = channel.check()

    assert any("imap" in line for line in report)
    assert any("smtp" in line for line in report)
    assert not any(line.startswith("FAIL") for line in report)


def test_the_check_SENDS_and_QUEUES_nothing(channel):
    from agent import worker

    channel.box = [(1, _mail("me@example.com", "do this", "please"))]
    channel.check()

    assert channel.outbox == []
    assert worker.tasks() == []


def test_the_check_REPORTS_an_empty_allowlist(channel, monkeypatch):
    from agent import config

    monkeypatch.setattr(config, "EMAIL_ALLOW", frozenset())
    report = channel.check()

    assert any(line.startswith("FAIL") and "authorises nobody" in line
               for line in report)


def test_the_check_WARNS_that_a_first_run_adopts_the_inbox(channel):
    """The surprising behaviour, said before it happens rather than after."""
    channel.box = [(1, _mail("me@example.com", "old", "x"))]

    assert any("adopt" in line for line in channel.check())


def test_an_unreachable_mailbox_is_a_FAIL_not_a_crash(channel, monkeypatch):
    def dead():
        raise channel.ChannelUnavailable("imap: authentication failed")

    monkeypatch.setattr(channel, "_imap", dead)
    report = channel.check()

    assert any(line.startswith("FAIL") and "authentication failed" in line
               for line in report)
