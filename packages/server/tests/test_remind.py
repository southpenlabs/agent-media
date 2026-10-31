"""Bring back… (remind.py): `POST /session/remind` and what it does to the rows,
Home's replies and the phone's `reminders` frame."""

from __future__ import annotations

import json
import time

import pytest

from agent_media_server import app, dashboard, quiet, remind, session_events, sessions

from test_contract import (AUTH, SID, SID2, call, server, shelf,  # noqa: F401
                           signed_in, typed)
from test_dashboard import _asked, _said, _transcript, machine  # noqa: F401
from test_session_events import _idle_watcher, screen  # noqa: F401
from test_thread_events import Stream


@pytest.fixture(autouse=True)
def _isolated(monkeypatch):
    """Never this machine's calendar or config (quiet's rule reads them)."""
    from agent_media_core import config

    quiet._reset_for_tests()
    monkeypatch.setattr(quiet, "_calendar_events", lambda: [])
    monkeypatch.setattr(config, "load", lambda path=None: {})
    yield
    quiet._reset_for_tests()


def _rows(server):
    _, targets = call(server, "GET", "/targets", headers=AUTH)
    return {r["session"]: r for r in targets["sessions"]}


def _put(session, at, held=None, set_at=None):
    """A reminder as the store keeps it, however long ago it was set."""
    remind.STORE.path().parent.mkdir(parents=True, exist_ok=True)
    rows = remind.STORE.all()
    rows[session] = {"at": at, "held": held or at - 100, "set": set_at or at - 100}
    remind.STORE.path().write_text(json.dumps(rows))


def test_a_reminder_is_set_held_and_cancelled(server, shelf, signed_in, typed):
    at = time.time() + 3600
    res, obj = call(server, "POST", "/session/remind",
                    {"session": SID, "at": at, "held": 1790000000.0}, AUTH)
    assert res.status == 200, obj
    assert obj["remind"] == {"at": round(at, 3), "held": 1790000000.0} and obj["reminded"] is False
    # Held down as a quiet until then: the lists sort it where it was.
    assert obj["quiet"] == {"until": round(at, 3), "at": 1790000000.0, "remind": True}
    row = _rows(server)[SID]
    assert row["remind"] == obj["remind"] and row["reminded"] is False
    assert row["quiet"] == obj["quiet"]
    other = _rows(server)[SID2]
    assert other["remind"] is None and other["reminded"] is False and other["quiet"] is None
    assert typed == []                                       # types nothing
    # Moved: the same place in the list.
    res, obj = call(server, "POST", "/session/remind",
                    {"session": SID, "at": at + 600, "held": 1790009999.0}, AUTH)
    assert obj["remind"] == {"at": round(at + 600, 3), "held": 1790000000.0}
    # Cancelled by false or null.
    for gone in (False, None):
        call(server, "POST", "/session/remind", {"session": SID, "at": at}, AUTH)
        res, obj = call(server, "POST", "/session/remind", {"session": SID, "at": gone}, AUTH)
        assert res.status == 200 and obj["remind"] is None and obj["quiet"] is None
        assert _rows(server)[SID]["remind"] is None


def test_a_real_quiet_stays_as_it_is_until_the_reminder_is_due(server, shelf, signed_in):
    until = time.time() + 86400
    quiet.set_quiet(SID, until, at=100.0)
    call(server, "POST", "/session/remind", {"session": SID, "at": time.time() + 60}, AUTH)
    assert _rows(server)[SID]["quiet"] == {"until": round(until, 3), "at": 100.0}
    # Due: the quiet is lifted whatever it says.
    _put(SID, time.time() - 5)
    row = _rows(server)[SID]
    assert row["reminded"] is True and row["quiet"] is None
    # Opened, which cancels it: the quiet takes hold again.
    call(server, "POST", "/session/remind", {"session": SID, "at": False}, AUTH)
    assert _rows(server)[SID]["quiet"] == {"until": round(until, 3), "at": 100.0}


def test_remind_refusals(server, shelf, signed_in, monkeypatch):
    now = time.time()
    res, obj = call(server, "POST", "/session/remind", {"session": "nope", "at": now + 60}, AUTH)
    assert res.status == 400 and obj == {"ok": False, "error": "not a session id"}
    for bad in (True, "soon", now - 10):
        res, obj = call(server, "POST", "/session/remind", {"session": SID, "at": bad}, AUTH)
        assert res.status == 400 and obj["error"] == "at must be a time to come, null or false"
    res, obj = call(server, "POST", "/session/remind",
                    {"session": SID, "at": now + 400 * 86400}, AUTH)
    assert res.status == 400 and obj["error"] == "at must be within a year"
    res, obj = call(server, "POST", "/session/remind",
                    {"session": SID, "at": now + 60, "held": now + 3600}, AUTH)
    assert res.status == 400 and obj["error"] == "held must be a time that has passed"
    from agent_media_server import matrix

    monkeypatch.setattr(sessions, "session_exists", lambda s: False)
    monkeypatch.setattr(matrix, "room", lambda s: None)
    other = "11111111-2222-4333-8444-555555555555"
    res, obj = call(server, "POST", "/session/remind", {"session": other, "at": now + 60}, AUTH)
    assert res.status == 404 and obj == {"ok": False, "error": "no such session 11111111"}
    assert not remind.STORE.path().exists()


def test_remind_is_gated_and_open_to_the_web_client(server, shelf, monkeypatch):
    from agent_media_server import auth_abs

    monkeypatch.setattr(auth_abs, "abs_urls", lambda: ["http://abs"])
    monkeypatch.setattr(auth_abs, "_abs_get", lambda *a, **k: (None, 401))
    res, obj = call(server, "POST", "/session/remind", {"session": SID, "at": time.time() + 60})
    assert res.status == 401 and obj["ok"] is False
    assert not remind.STORE.path().exists()
    assert "/session/remind" in app.CORS_PATHS


def test_pending_reminders_are_left_out_of_replies_and_due_ones_are_in(server, shelf, signed_in,
                                                                      machine):
    _transcript(SID, [_asked("hi", 1), _said("All done.", 2)])
    _, obj = call(server, "GET", "/dashboard", headers=AUTH)
    assert [r["session"] for r in obj["replies"]] == [SID]
    reply_at = obj["replies"][0]["at"]
    # Pending: held down, in no list of news; recent carries it.
    call(server, "POST", "/session/remind", {"session": SID, "at": time.time() + 600}, AUTH)
    _, obj = call(server, "GET", "/dashboard", headers=AUTH)
    assert obj["replies"] == []
    rec = {r["session"]: r for r in obj["recent"]}[SID]
    assert rec["remind"]["at"] > time.time() and rec["reminded"] is False
    assert rec["quiet"]["remind"] is True
    # Due: back, `at` the later of the reply's and the reminder's, whatever the quiet says.
    quiet.set_quiet(SID, None, at=100.0)
    due = time.time() - 2
    _put(SID, due)
    _, obj = call(server, "GET", "/dashboard", headers=AUTH)
    got = [r for r in obj["replies"] if r["session"] == SID]
    assert len(got) == 1 and got[0]["reminded"] is True and got[0]["at"] >= reply_at
    rec = {r["session"]: r for r in obj["recent"]}[SID]
    assert rec["reminded"] is True and rec["quiet"] is None


def test_a_due_thread_that_ends_on_no_reply_still_comes_back(server, shelf, signed_in, machine):
    _transcript(SID, [_asked("hi", 1), _said("All done.", 2), _asked("more", 3)])
    _put(SID, time.time() - 5)
    _, obj = call(server, "GET", "/dashboard", headers=AUTH)
    got = [r for r in obj["replies"] if r["session"] == SID]
    assert len(got) == 1 and got[0]["reminded"] is True and got[0]["title"] == "Sasonica music"


def test_a_due_reminder_is_a_reminders_frame_for_a_phone_that_asked(server, screen, monkeypatch):
    from test_mic import _device

    pixel, _ = _device("Pixel 8a")
    st = Stream(server, "/sessions/events?ping=0.3&remind=1", pixel)
    try:
        # Nothing due on connecting: nothing sent but the sessions.
        st.next("sessions", 2.0)
        call(server, "POST", "/session/remind", {"session": SID2, "at": time.time() + 0.6}, AUTH)
        got = st.next("reminders", 3.0)
        assert [(d["session"], d["title"]) for d in got["due"]] == [(SID2, "Sasonica web")]
        # Opened, so cancelled: the set empties.
        call(server, "POST", "/session/remind", {"session": SID2, "at": False}, AUTH)
        assert st.next("reminders", 3.0)["due"] == []
    finally:
        st.close()
    # A phone that connects with one due is told at once; one that did not ask is not.
    _put(SID2, time.time() - 5)
    st = Stream(server, "/sessions/events?ping=0.3&remind=1", pixel)
    try:
        assert [d["session"] for d in st.next("reminders", 2.0)["due"]] == [SID2]
    finally:
        st.close()


def test_cancelling_drops_the_row_and_is_idempotent():
    remind.set_remind(SID, time.time() + 60)
    assert SID in remind.reminders()
    assert remind.set_remind(SID, None) is True and remind.reminders() == {}


def test_a_due_reminder_unarchives_its_thread_once(server, shelf, signed_in, machine):
    """David, 31 Oct 2026: "does bring back also unarchive?" — yes, when due."""
    from agent_media_server import archive

    _transcript(SID, [_asked("hi", 1), _said("All done.", 2)])
    archive.set_archived(SID, True)
    call(server, "POST", "/session/remind", {"session": SID, "at": time.time() + 600}, AUTH)
    # Pending: still archived, and not news.
    assert archive.is_archived(SID) and _rows(server)[SID]["archived"] is True
    _, obj = call(server, "GET", "/dashboard", headers=AUTH)
    assert not any(r["session"] == SID for r in obj["replies"])
    # Due: unarchived by the server, in the list and in Home's replies.
    _put(SID, time.time() - 2)
    row = _rows(server)[SID]
    assert row["archived"] is False and row["reminded"] is True and not archive.is_archived(SID)
    dashboard._reset_for_tests()                 # a fresh index; the next test uses a cached one
    _, obj = call(server, "GET", "/dashboard", headers=AUTH)
    assert [r["reminded"] for r in obj["replies"] if r["session"] == SID] == [True]
    assert remind.STORE.get(SID).get("fired")
    # Archived again after it came back: left archived (fired once).
    archive.set_archived(SID, True)
    assert _rows(server)[SID]["archived"] is True and remind.fire_due() == []


def test_the_dashboard_unarchives_with_a_cached_index(server, shelf, signed_in, machine):
    """The index is cached for a few seconds; a thread just brought back is
    in the replies whatever the cache still says."""
    from agent_media_server import archive

    _transcript(SID, [_asked("hi", 1), _said("All done.", 2)])
    archive.set_archived(SID, True)
    _, obj = call(server, "GET", "/dashboard", headers=AUTH)   # caches the index: archived
    assert not any(r["session"] == SID for r in obj["replies"])
    _put(SID, time.time() - 2)
    _, obj = call(server, "GET", "/dashboard", headers=AUTH)
    got = [r for r in obj["replies"] if r["session"] == SID]
    assert len(got) == 1 and got[0]["reminded"] is True and got[0]["text"] == "All done."
    assert SID in {r["session"] for r in obj["recent"]}


def test_the_reminders_frame_unarchives_with_nobody_reading_the_list(server, screen, monkeypatch):
    from agent_media_server import archive
    from test_mic import _device

    archive.set_archived(SID2, True)
    _put(SID2, time.time() - 5)
    pixel, _ = _device("Pixel 8a")
    st = Stream(server, "/sessions/events?ping=0.3&remind=1", pixel)
    try:
        assert [d["session"] for d in st.next("reminders", 2.0)["due"]] == [SID2]
    finally:
        st.close()
    assert not archive.is_archived(SID2)
