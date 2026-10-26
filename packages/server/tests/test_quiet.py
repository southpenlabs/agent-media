"""Quiet for… (quiet.py): `POST /session/quiet` and the rows' `quiet`."""

from __future__ import annotations

import json
import time

from agent_media_server import app, quiet, sessions

from test_contract import (AUTH, SID, SID2, call, server, shelf,  # noqa: F401
                           signed_in, typed)


def _rows(server):
    _, targets = call(server, "GET", "/targets", headers=AUTH)
    return {r["session"]: r for r in targets["sessions"]}


def test_quiet_for_an_hour_until_off_and_ended(server, shelf, signed_in, typed):
    until = time.time() + 3600
    res, obj = call(server, "POST", "/session/quiet",
                    {"session": SID2, "until": until, "at": 1790000000.0}, AUTH)
    assert res.status == 200, obj
    assert obj == {"ok": True, "session": SID2,
                   "quiet": {"until": round(until, 3), "at": 1790000000.0}}
    by = _rows(server)
    assert by[SID2]["quiet"] == {"until": round(until, 3), "at": 1790000000.0}
    assert by[SID]["quiet"] is None
    _, conv = call(server, "GET", "/conversations", headers=AUTH)
    assert {r["session"]: r["quiet"] for r in conv["sessions"]}[SID2]["at"] == 1790000000.0
    assert typed == []                                        # quieting types nothing
    # Until I turn it off: a new end, the same place in the list.
    res, obj = call(server, "POST", "/session/quiet",
                    {"session": SID2, "until": None, "at": 1790009999.0}, AUTH)
    assert obj["quiet"] == {"until": None, "at": 1790000000.0}
    # End quiet.
    res, obj = call(server, "POST", "/session/quiet", {"session": SID2, "until": False}, AUTH)
    assert res.status == 200 and obj == {"ok": True, "session": SID2, "quiet": None}
    assert _rows(server)[SID2]["quiet"] is None
    # Absent `until` is until turned off; absent `at` is now.
    before = time.time()
    _, obj = call(server, "POST", "/session/quiet", {"session": SID}, AUTH)
    assert obj["quiet"]["until"] is None and obj["quiet"]["at"] >= round(before, 3) - 0.001


def test_an_expired_quiet_is_ignored_and_pruned(monkeypatch):
    now = time.time()
    quiet.QUIET.path().parent.mkdir(parents=True, exist_ok=True)
    quiet.QUIET.path().write_text(json.dumps({
        SID: {"until": now - 5, "at": 1.0, "set": now - 100},
        "junk": "not a row"}))
    assert quiet.quieted() == {} and quiet.quiet_of(SID) is None
    assert quiet.set_quiet(SID2, now + 60, at=2.0) is True
    stored = json.loads(quiet.QUIET.path().read_text())
    assert set(stored) == {SID2}                              # the expired and the junk went
    # An expired quiet set again starts afresh: its time is the new one.
    quiet.QUIET.path().write_text(json.dumps({SID: {"until": now - 5, "at": 1.0, "set": 0}}))
    quiet.set_quiet(SID, None, at=3.0)
    assert quiet.quiet_of(SID) == {"until": None, "at": 3.0}
    assert quiet.set_quiet(SID, False) is True and quiet.set_quiet(SID, False) is False


def test_quiet_refusals(server, shelf, signed_in, monkeypatch):
    res, obj = call(server, "POST", "/session/quiet", {"session": "nope"}, AUTH)
    assert res.status == 400 and obj == {"ok": False, "error": "not a session id"}
    for bad in (True, "soon", time.time() - 10):
        res, obj = call(server, "POST", "/session/quiet", {"session": SID, "until": bad}, AUTH)
        assert res.status == 400 and obj["error"] == "until must be a time to come, null or false"
    res, obj = call(server, "POST", "/session/quiet",
                    {"session": SID, "until": None, "at": time.time() + 3600}, AUTH)
    assert res.status == 400 and obj["error"] == "at must be a time that has passed"
    from agent_media_server import matrix

    monkeypatch.setattr(sessions, "session_exists", lambda s: False)
    monkeypatch.setattr(matrix, "room", lambda s: None)
    other = "11111111-2222-4333-8444-555555555555"
    res, obj = call(server, "POST", "/session/quiet", {"session": other}, AUTH)
    assert res.status == 404 and obj == {"ok": False, "error": "no such session 11111111"}
    assert not quiet.QUIET.path().exists()


def test_quiet_is_gated_and_open_to_the_web_client(server, shelf, monkeypatch):
    from agent_media_server import auth_abs

    monkeypatch.setattr(auth_abs, "abs_urls", lambda: ["http://abs"])
    monkeypatch.setattr(auth_abs, "_abs_get", lambda *a, **k: (None, 401))
    res, obj = call(server, "POST", "/session/quiet", {"session": SID})
    assert res.status == 401 and obj["ok"] is False
    assert not quiet.QUIET.path().exists()
    assert "/session/quiet" in app.CORS_PATHS
