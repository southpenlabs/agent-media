"""Mute (mute.py): `POST /session/mute`, the rows' `muted`, Local chats'
settings, and expiry."""

from __future__ import annotations

import json
import time

import pytest

from agent_media_server import devices, mute

from test_contract import (AUTH, SID, SID2, call, server, shelf,  # noqa: F401
                           signed_in, typed)

WA = "local:whatsapp:61400000000@s.whatsapp.net"


def _rows(server):
    _, targets = call(server, "GET", "/targets", headers=AUTH)
    return {r["session"]: r for r in targets["sessions"]}


def test_mute_until_then_until_off_and_ended(server, shelf, signed_in, typed):
    until = time.time() + 3600
    res, obj = call(server, "POST", "/session/mute",
                    {"session": SID2, "muted": True, "until": until}, AUTH)
    assert res.status == 200, obj
    assert obj == {"ok": True, "session": SID2, "muted": {"until": round(until, 3)}}
    by = _rows(server)
    assert by[SID2]["muted"] == {"until": round(until, 3)}
    assert by[SID]["muted"] is False
    assert by[SID2]["quiet"] is None                       # not a quiet
    _, conv = call(server, "GET", "/conversations", headers=AUTH)
    assert {r["session"]: r["muted"] for r in conv["sessions"]}[SID2]
    assert typed == []
    # Until I turn it off.
    _, obj = call(server, "POST", "/session/mute", {"session": SID2, "muted": True}, AUTH)
    assert obj["muted"] == {"until": None}
    # Unmute.
    res, obj = call(server, "POST", "/session/mute", {"session": SID2, "muted": False}, AUTH)
    assert res.status == 200 and obj == {"ok": True, "session": SID2, "muted": False}
    assert _rows(server)[SID2]["muted"] is False


@pytest.mark.parametrize("body,err", [
    ({"session": "nope"}, "not a session id"),
    ({"session": SID}, "muted must be true or false"),
    ({"session": SID, "muted": "yes"}, "muted must be true or false"),
    ({"session": SID, "muted": True, "until": 5}, "until must be a time to come or null"),
    ({"session": SID, "muted": True, "until": True}, "until must be a time to come or null"),
])
def test_bad_bodies_are_refused(server, shelf, signed_in, body, err):
    res, obj = call(server, "POST", "/session/mute", body, AUTH)
    assert res.status == 400 and obj["error"] == err


def test_unknown_session_and_no_bearer(server, shelf, signed_in, monkeypatch):
    from agent_media_server import matrix, sessions

    monkeypatch.setattr(sessions, "session_exists", lambda s: False)
    monkeypatch.setattr(matrix, "room", lambda s: None)
    other = "11111111-2222-4333-8444-555555555555"
    res, obj = call(server, "POST", "/session/mute", {"session": other, "muted": True}, AUTH)
    assert res.status == 404 and obj == {"ok": False, "error": "no such session 11111111"}
    assert not mute.MUTE.path().exists()
    res, _ = call(server, "POST", "/session/mute", {"session": SID, "muted": True})
    assert res.status == 401


def test_an_expired_mute_is_ignored_and_pruned():
    now = time.time()
    mute.MUTE.path().parent.mkdir(parents=True, exist_ok=True)
    mute.MUTE.path().write_text(json.dumps({
        SID: {"until": now - 5, "set": now - 100}, "junk": "not a row"}))
    assert mute.muted() == {} and mute.mute_of(SID) is None
    assert mute.add_mute([{"session": SID}]) == [{"session": SID, "muted": False}]
    assert mute.set_mute(SID2, True, now + 60) is True
    assert set(json.loads(mute.MUTE.path().read_text())) == {SID2}
    assert mute.is_muted(SID2) and not mute.is_muted(SID2, now + 120)   # runs out


def test_a_local_chat_mutes_by_id_and_says_so_in_its_settings(server, shelf):
    code, _ = devices.mint_code("Pixel 8a")
    _, phone = call(server, "POST", "/pair", {"code": code, "device": "Pixel 8a"})
    auth = {"Authorization": f"Bearer {phone['token']}"}
    res, obj = call(server, "POST", "/session/mute", {"session": WA, "muted": True}, auth)
    assert res.status == 404, obj                          # not listed yet
    res, obj = call(server, "POST", "/local/chats", {"network": "whatsapp", "chats": [
        {"id": "61400000000@s.whatsapp.net", "name": "Ana", "kind": "person"}]}, auth)
    assert res.status == 200, obj
    assert obj["chats"][0]["settings"]["muted"] is False
    res, obj = call(server, "POST", "/session/mute", {"session": WA, "muted": True}, auth)
    assert res.status == 200 and obj["muted"] == {"until": None}
    _, got = call(server, "GET", "/local/chats", headers=auth)
    assert {c["id"]: c for c in got["chats"]}[WA]["settings"]["muted"] == {"until": None}
    _, targets = call(server, "GET", "/targets", headers=auth)
    assert next(r for r in targets["sessions"] if r["session"] == WA)["muted"] == {"until": None}


def test_features_say_so(server, signed_in):
    _, obj = call(server, "GET", "/features", headers=AUTH)
    assert obj["mute"] is True


# --- latest wins (flag_clock.py) -------------------------------------------------

def test_an_older_change_is_ignored(server, shelf, signed_in):
    now = time.time()
    res, obj = call(server, "POST", "/session/mute",
                    {"session": SID, "muted": True, "at": now - 10}, AUTH)
    assert res.status == 200 and obj["muted"] == {"until": None}
    # The network's unmute, made before it: stale, not applied.
    res, obj = call(server, "POST", "/session/mute",
                    {"session": SID, "muted": False, "at": now - 20}, AUTH)
    assert res.status == 200 and obj == {"ok": True, "session": SID, "stale": True,
                                         "changed_at": round(now - 10, 3)}
    assert mute.is_muted(SID)
    # A newer one applies; one with no time is now.
    res, obj = call(server, "POST", "/session/mute",
                    {"session": SID, "muted": False, "changed_at": now - 5}, AUTH)
    assert obj["muted"] is False
    _, obj = call(server, "POST", "/session/mute", {"session": SID, "muted": True}, AUTH)
    assert obj["muted"] == {"until": None}
    _, obj = call(server, "POST", "/session/mute",
                  {"session": SID, "muted": False, "at": now - 1}, AUTH)
    assert obj["stale"] is True


def test_a_refused_change_does_not_move_the_clock(server, shelf, signed_in):
    from agent_media_server import flag_clock

    now = time.time()
    res, _ = call(server, "POST", "/session/mute",
                  {"session": SID, "muted": "x", "at": now}, AUTH)
    assert res.status == 400
    assert flag_clock.CLOCK.get(SID) is None
    res, obj = call(server, "POST", "/session/mute",
                    {"session": SID, "muted": True, "at": now + 3600}, AUTH)
    assert res.status == 400 and obj["error"] == "changed_at must be a time that has passed"


def test_mark_fields_each_have_their_own_clock_and_archive_one_too(server, shelf, signed_in):
    from agent_media_server import archive

    now = time.time()
    _, obj = call(server, "POST", "/session/mark",
                  {"session": SID, "top": True, "changed_at": now - 10}, AUTH)
    assert obj["ok"] and obj["top"]
    # unread is new, top is stale: only unread applies.
    _, obj = call(server, "POST", "/session/mark",
                  {"session": SID, "top": False, "unread": True, "changed_at": now - 20}, AUTH)
    assert obj["ok"] and not obj.get("stale")
    row = _rows(server)[SID]
    assert row["top"] and row["marked_unread"] is True
    _, obj = call(server, "POST", "/session/archive",
                  {"session": SID, "archived": True, "at": now - 10}, AUTH)
    assert obj["ok"] and SID in archive.archived()
    _, obj = call(server, "POST", "/session/archive",
                  {"session": SID, "archived": False, "at": now - 30}, AUTH)
    assert obj["stale"] is True and SID in archive.archived()


def test_quiet_keeps_its_own_at_and_takes_changed_at(server, shelf, signed_in):
    now = time.time()
    _, obj = call(server, "POST", "/session/quiet",
                  {"session": SID, "until": None, "at": 1790000000.0,
                   "changed_at": now - 10}, AUTH)
    assert obj["quiet"]["at"] == 1790000000.0
    _, obj = call(server, "POST", "/session/quiet",
                  {"session": SID, "until": False, "changed_at": now - 20}, AUTH)
    assert obj["stale"] is True


def test_a_stale_change_still_needs_a_bearer(server, shelf, signed_in):
    now = time.time()
    call(server, "POST", "/session/mute", {"session": SID, "muted": True, "at": now}, AUTH)
    res, obj = call(server, "POST", "/session/mute",
                    {"session": SID, "muted": False, "at": now - 50})
    assert res.status == 401 and "stale" not in obj
