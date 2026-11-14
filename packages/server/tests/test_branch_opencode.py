"""Branching an opencode chat (branch.opencode_cut): where opencode's fork
cuts, it keeping every message before the one it is given (measured on
opencode 1.19.5, 15 Nov 2026)."""

import json
import sqlite3

import pytest

from agent_media_server import branch, driver

OC = "ses_e23e7b8d4ffeZNe9O2VSU7VpMI"
T0 = 1794693943605
# Two turns; the second reply took two steps.
ROWS = [("msg_a1", "user"), ("msg_a2", "assistant"), ("msg_b1", "user"),
        ("msg_b2", "assistant"), ("msg_b3", "assistant")]


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    path = tmp_path / "data" / "opencode" / "opencode.db"
    path.parent.mkdir(parents=True)
    with sqlite3.connect(path) as c:
        c.executescript("""
            create table session (id text primary key, parent_id text, directory text,
                title text, time_created integer, time_updated integer, time_archived integer);
            create table message (id text primary key, session_id text, time_created integer,
                data text);
            create table part (id text primary key, message_id text, session_id text,
                time_created integer, time_updated integer, data text);
        """)
        c.execute("insert into session values (?,null,?,'t',?,?,null)", (OC, str(tmp_path), T0, T0))
        for n, (mid, role) in enumerate(ROWS):
            c.execute("insert into message values (?,?,?,?)",
                      (mid, OC, T0 + n, json.dumps({"role": role})))
    return path


def test_with_history_keeps_the_reply_and_cuts_at_the_next_message(db):
    assert branch.opencode_cut(OC, "msg_a2", "history") == (True, "msg_b1")
    assert branch.opencode_cut(OC, "msg_b2", "history") == (True, "")   # the last reply: all
    assert branch.opencode_cut(OC, "msg_zz", "history") == (False, "")


def test_before_a_message_cuts_at_it_and_the_first_has_nothing_before(db):
    assert branch.opencode_cut(OC, "msg_b1", "before") == (True, "msg_b1")
    assert branch.opencode_cut(OC, "msg_a1", "before") == (True, "")
    assert branch.opencode_cut(OC, "msg_b2", "before") == (False, "")    # not the person's


def test_session_branch_starts_the_fork_headless(db, monkeypatch):
    from agent_media_server import auth, sessions

    monkeypatch.setattr(auth, "gate", lambda b: ({"id": "u"}, {}))
    monkeypatch.setattr(sessions, "transcript_cwd", lambda s: "/w")
    seen = {}

    class Fake:
        def start(self, **kw):
            seen.update(kw)
            return True, {"session": "ses_new"}

    monkeypatch.setattr(driver, "headless_driver", lambda: Fake())
    ok, d = branch.session_branch(OC, "msg_a2", "what next?", "t", mode="history")
    assert ok and d["branched"] and d["from"] == {"session": OC, "id": "msg_a2"}
    assert seen["agent"] == "opencode" and seen["fork_from"] == OC and seen["fork_at"] == "msg_b1"
