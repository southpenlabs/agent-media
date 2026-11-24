"""Triggers by place (place_triggers.py, maps step 11): a circle, arrive or leave, and
what to do; crossings decided on the server from every fix it gets."""
from __future__ import annotations

import time

import pytest

from agent_media_server import place_triggers as pt

from test_contract import call, server, shelf, signed_in, typed  # noqa: F401
from test_mic import _device

HOME = (-37.9000, 145.0000)
#: About 1.1 km north of HOME: well outside a 150 m circle.
AWAY = (-37.8900, 145.0000)
#: About 130 m north: inside 150 m, but within the leave margin.
EDGE = (-37.89883, 145.0000)


@pytest.fixture()
def fired(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    got: list = []
    monkeypatch.setattr(pt, "RUN", lambda r, how: got.append((r["id"], how, r["do"])))
    return got


def _add(path, **kw):
    body = {"name": "Home", "lat": HOME[0], "lon": HOME[1], "do": "say", "text": "welcome home", **kw}
    ok, out = pt.change("", body, path)
    assert ok, out
    return out["trigger"]


def test_arrive_fires_once_on_crossing_not_on_first_fix(tmp_path, fired):
    p = tmp_path / "t.json"
    t = _add(p)
    now = time.time()
    # The first fix only says where the phone is: at home already, nothing fires.
    assert pt.on_fix("", "dev1", *HOME, 10, now - 600, p, now) == []
    assert pt.on_fix("", "dev1", *AWAY, 10, now - 500, p, now) == []
    assert pt.on_fix("", "dev1", *HOME, 10, now - 10, p, now) == [t["id"]]
    assert fired == [(t["id"], "arrive", "say")]
    # Wobbling on the edge, and back in within the quiet time: nothing more.
    assert pt.on_fix("", "dev1", *EDGE, 10, now - 5, p, now) == []
    assert pt.on_fix("", "dev1", *AWAY, 10, now - 4, p, now) == []
    assert pt.on_fix("", "dev1", *HOME, 10, now - 3, p, now) == []
    assert len(fired) == 1
    # After the quiet time it fires again.
    later = now + 3600
    pt.on_fix("", "dev1", *AWAY, 10, later - 100, p, later)
    assert pt.on_fix("", "dev1", *HOME, 10, later - 50, p, later) == [t["id"]]


def test_leave_and_rough_fixes(tmp_path, fired):
    p = tmp_path / "t.json"
    t = _add(p, on="leave")
    now = time.time()
    pt.on_fix("", "d", *HOME, 10, now - 60, p, now)
    # A fix too rough to say: nothing changes.
    assert pt.on_fix("", "d", *AWAY, 2000, now - 30, p, now) == []
    assert pt.on_fix("", "d", *AWAY, 20, now - 20, p, now) == [t["id"]]
    assert fired[-1][1] == "leave"


def test_stale_batch_sets_state_but_fires_nothing(tmp_path, fired):
    p = tmp_path / "t.json"
    _add(p)
    now = time.time()
    pts = [(now - 7200, *AWAY, 10.0), (now - 3600, *HOME, 10.0)]
    assert pt.on_fixes("", "d", pts, p, now) == []
    assert fired == []
    # Already home: a fresh fix there fires nothing either.
    assert pt.on_fix("", "d", *HOME, 10, now, p, now) == []


def test_remind_is_once_and_devices_are_apart(tmp_path, fired):
    p = tmp_path / "t.json"
    t = _add(p, do="remind", text="buy screws", name="Bunnings")
    assert t["once"] is True
    now = time.time()
    pt.on_fix("", "a", *AWAY, 10, now - 60, p, now)
    pt.on_fix("", "b", *HOME, 10, now - 60, p, now)  # b was already there
    assert pt.on_fix("", "a", *HOME, 10, now - 5, p, now) == [t["id"]]
    row = pt.listing("", p)[1]["triggers"][0]
    assert row["off"] is True and row["count"] == 1
    assert pt.regions("", p)[1]["regions"] == []


def test_validation_and_regions(tmp_path, fired):
    p = tmp_path / "t.json"
    assert pt.change("", {"name": "x", "lat": 99, "lon": 0}, p)[1]["code"] == "bad_point"
    assert pt.change("", {"name": "", "lat": 1, "lon": 1}, p)[1]["code"] == "bad_name"
    assert pt.change("", {"name": "x", "lat": 1, "lon": 1, "radius_m": 10, "text": "t"}, p)[1]["code"] == "bad_radius"
    assert pt.change("", {"name": "x", "lat": 1, "lon": 1, "do": "remind"}, p)[1]["code"] == "bad_text"
    assert pt.change("", {"name": "x", "lat": 1, "lon": 1, "do": "dance"}, p)[1]["code"] == "bad_do"
    assert pt.change("", {"name": "x", "lat": 1, "lon": 1, "do": "say", "text": "t", "uri": "yt:x"}, p)[1]["code"] == "bad_uri"
    t = _add(p, do="music", text="", uri="yt:https://youtu.be/abc")
    reg = pt.regions("", p)[1]
    assert reg["regions"] == [{"id": t["id"], "lat": HOME[0], "lon": HOME[1], "radius_m": 150}]
    assert "text" not in reg["regions"][0] and reg["version"]
    # Changed: the version moves; another account sees none of it.
    ok, out = pt.change("", {"id": t["id"], "radius_m": 300}, p)
    assert ok and out["trigger"]["radius_m"] == 300 and out["trigger"]["uri"] == "yt:https://youtu.be/abc"
    assert pt.regions("", p)[1]["version"] != reg["version"]
    assert pt.regions("acct-other", p)[1]["regions"] == []
    assert pt.change("", {"id": t["id"], "delete": True}, p)[1] == {"deleted": t["id"]}
    assert pt.change("", {"id": t["id"], "delete": True}, p)[1]["status"] == 404


def test_routes(server, fired):
    pixel, _pid = _device("Pixel 8a")
    res, obj = call(server, "POST", "/map/triggers", {"name": "Home", "lat": HOME[0], "lon": HOME[1],
                                                      "do": "catchup"}, headers=pixel)
    assert res.status == 200, obj
    tid = obj["trigger"]["id"]
    assert [t["id"] for t in call(server, "GET", "/map/triggers", headers=pixel)[1]["triggers"]] == [tid]
    reg = call(server, "GET", "/device/places/watch", headers=pixel)[1]
    assert reg["ok"] and [r["id"] for r in reg["regions"]] == [tid]
    now = time.time()
    # The background share's fix sets where the phone is; the phone's own crossing fires it.
    assert call(server, "POST", "/device/location", {"lat": AWAY[0], "lon": AWAY[1], "at": now - 60},
                headers=pixel)[0].status == 200
    res, obj = call(server, "POST", "/device/places/cross", {"lat": HOME[0], "lon": HOME[1],
                                                             "accuracy_m": 15, "at": now}, headers=pixel)
    assert res.status == 200 and obj["fired"] == [tid]
    assert fired == [(tid, "arrive", "catchup")]
    # A history batch counts too.
    res, _ = call(server, "POST", "/device/location/history",
                  {"points": [{"lat": AWAY[0], "lon": AWAY[1], "at": now + 1}]}, headers=pixel)
    assert res.status == 200
    assert call(server, "POST", "/device/places/cross", {"nope": 1}, headers=pixel)[0].status == 400
    assert call(server, "GET", "/device/places/watch")[0].status == 401
