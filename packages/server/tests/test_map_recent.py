"""GET /map/recent_places (map_recent.py): places recent chats pinned, for the Map's "Where to?"."""
from __future__ import annotations

from agent_media_server import devices, map_recent, sessions, transcript

from test_contract import call, server, shelf, signed_in, typed  # noqa: F401
from test_mic import _device

OLD = "11111111-1111-1111-1111-111111111111"
NEW = "22222222-2222-2222-2222-222222222222"
SEALED = "33333333-3333-3333-3333-333333333333"


def _msg(mid, at, *points, kind="pins"):
    return {"id": mid, "role": "assistant", "at": at, "parts": [{"type": "text", "text": "x"}],
            "places": [{"kind": kind, "points": [{"name": n, "lat": la, "lon": lo} for n, la, lo in points]}]}


CHATS = {
    OLD: [_msg("o1", 100.0, ("Brunetti", -37.7981, 144.9687)), _msg("o2", 300.0, ("Seven Seeds", -37.8003, 144.9581))],
    NEW: [_msg("n1", 200.0, ("Brunetti", -37.79811, 144.96871)),  # the same place as o1, mentioned later
          _msg("n2", 400.0, ("Home", -37.81, 144.96), ("Park", -37.80, 144.97), kind="route")],
    SEALED: [_msg("s1", 500.0, ("Secret", -37.0, 145.0))],
}


def _setup(monkeypatch):
    map_recent._reset_for_tests()
    rows = [{"session": OLD, "title": "Coffee"}, {"session": NEW, "title": "A walk"},
            {"session": SEALED, "sealed": True}]
    monkeypatch.setitem(sessions._BUILDS, "", {"out": {"sessions": rows}})
    mtimes = {OLD: 10.0, NEW: 20.0, SEALED: 30.0}
    monkeypatch.setattr(transcript, "file_state", lambda s: (1, 1, mtimes[s]))
    asked = []

    def messages(s, **k):
        asked.append(s)
        return ([dict(m) for m in CHATS[s]], False)
    monkeypatch.setattr(transcript, "messages", messages)
    return asked, mtimes


def test_recent_chats_places_newest_first_one_each(server, monkeypatch):
    asked, mtimes = _setup(monkeypatch)
    pixel, _ = _device("Pixel 8a")
    res, obj = call(server, "GET", "/map/recent_places", headers=pixel)
    assert res.status == 200
    got = obj["places"]
    assert [p["name"] for p in got] == ["Home", "Park", "Seven Seeds", "Brunetti"]
    assert got[0] == {"name": "Home", "lat": -37.81, "lon": 144.96, "session": NEW, "msg": "n2", "at": 400.0, "chat": "A walk"}
    # Brunetti twice within 50 m: kept once, the newer mention (n1 at 200 beats o1 at 100).
    assert got[3]["msg"] == "n1" and got[3]["chat"] == "A walk"
    assert SEALED not in asked
    # Asked again with nothing changed: no transcript read again.
    asked.clear()
    assert call(server, "GET", "/map/recent_places?limit=2", headers=pixel)[1]["places"][1]["name"] == "Park"
    assert asked == []
    mtimes[OLD] = 50.0
    call(server, "GET", "/map/recent_places", headers=pixel)
    assert asked == [OLD]


def test_bad_limit_guest_and_no_bearer(server, monkeypatch):
    _setup(monkeypatch)
    pixel, _ = _device("Pixel 8a")
    for bad in ("0", "21", "x"):
        res, obj = call(server, "GET", f"/map/recent_places?limit={bad}", headers=pixel)
        assert res.status == 400 and obj["code"] == "bad_limit"
    hdrs, dev_id = _device("Visitor tablet")
    assert devices.set_role(dev_id, "guest")
    assert call(server, "GET", "/map/recent_places", headers=hdrs)[0].status == 403
    assert call(server, "GET", "/map/recent_places")[0].status == 401
