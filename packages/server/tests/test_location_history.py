"""Location history (location_history.py, maps proposal step 5): the phone's
batches, visits, names, retention, forgetting and the gate."""
from __future__ import annotations

import datetime as dt
import os
import stat
import time

import pytest

from agent_media_server import devices, location_history as lh, map_saved

from test_contract import call, server, shelf, signed_in, typed  # noqa: F401
from test_mic import _device

HOST = {"X-Auth-Token": "hosttok"}
HOME = (-37.8000, 144.9600)
SHOP = (-37.9100, 145.1200)  # ~18 km away


def _noon(days_ago: int = 0) -> float:
    d = dt.date.today() - dt.timedelta(days=days_ago)
    return time.mktime(d.timetuple()) + 12 * 3600


def _day(days_ago: int = 0) -> str:
    return (dt.date.today() - dt.timedelta(days=days_ago)).isoformat()


def _stay(at: float, where, minutes: int, every_s: int = 300, jitter: float = 0.0002) -> list[dict]:
    out = []
    for i in range(minutes * 60 // every_s + 1):
        out.append({"lat": where[0] + (jitter if i % 2 else 0), "lon": where[1], "accuracy_m": 12,
                    "at": at + i * every_s})
    return out


def _walk(at: float, a, b, steps: int = 10, every_s: int = 60) -> list[dict]:
    return [{"lat": a[0] + (b[0] - a[0]) * i / steps, "lon": a[1] + (b[1] - a[1]) * i / steps,
             "accuracy_m": 8, "at": at + i * every_s} for i in range(1, steps + 1)]


@pytest.fixture()
def state(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    monkeypatch.setenv("AMUX_AUTH_TOKEN", "hosttok")
    return tmp_path


def test_stays_group_a_still_run_and_skip_a_short_one():
    t = 1_000_000.0
    pts = [(t + i * 60, HOME[0], HOME[1]) for i in range(10)]           # 9 min at home
    pts += [(t + 600 + i * 30, SHOP[0] + i * 0.01, SHOP[1]) for i in range(5)]  # moving
    pts += [(t + 900, SHOP[0], SHOP[1]), (t + 1000, SHOP[0], SHOP[1])]  # 100 s: too short
    got = lh.stays(pts)
    assert len(got) == 1 and got[0]["from"] == t and got[0]["to"] == t + 540


def test_a_day_its_visits_names_and_distance(server, state):
    pixel, _ = _device("Pixel 8a")
    map_saved.change("", {"id": "home", "name": "Home", "lat": HOME[0], "lon": HOME[1]})
    t = _noon(2)
    pts = _stay(t, HOME, 30) + _walk(t + 1800, HOME, SHOP, steps=20) + _stay(t + 3100, SHOP, 60)
    res, obj = call(server, "POST", "/device/location/history", {"points": pts}, pixel)
    assert res.status == 200 and obj["kept"] == len(pts)
    assert stat.S_IMODE(os.stat(state / "agent-media" / "location.db").st_mode) == 0o600

    got = call(server, "GET", f"/map/history?day={_day(2)}", headers=pixel)[1]
    assert [v["place"] for v in got["visits"]] == ["Home", None]
    assert got["visits"][0]["place_from"] == "saved" and got["visits"][0]["minutes"] == 30
    assert 17_000 < got["distance_m"] < 22_000
    assert 2 < len(got["points"]) <= len(pts)  # thinned: the still points mostly dropped

    # The shop named by one lookup, cached: a second visit there asks nothing.
    asked: list = []
    assert lh.name_visits("", lookup=lambda la, lo: asked.append((la, lo)) or "Southland") == 1
    t2 = _noon(1)
    call(server, "POST", "/device/location/history", {"points": _stay(t2, SHOP, 20)}, pixel)
    assert lh.name_visits("", lookup=lambda la, lo: asked.append((la, lo)) or "Elsewhere") == 1
    assert len(asked) == 1
    vis = call(server, "GET", "/map/history/visits?q=southland", headers=HOST)[1]["visits"]
    assert [v["place"] for v in vis] == ["Southland", "Southland"] and vis[0]["from"] > vis[1]["from"]
    assert call(server, "GET", f"/map/history/visits?q=southland&from={_day(1)}", headers=HOST)[1]["visits"][0]["from"] == round(t2)
    assert call(server, "GET", "/map/history/visits?q=bunnings", headers=HOST)[1]["visits"] == []


def test_a_later_batch_extends_a_visit_and_keeps_its_name(server, state):
    pixel, _ = _device("Pixel 8a")
    t = _noon(1)
    call(server, "POST", "/device/location/history", {"points": _stay(t, SHOP, 10)}, pixel)
    lh.name_visits("", lookup=lambda la, lo: "Southland")
    call(server, "POST", "/device/location/history", {"points": _stay(t + 900, SHOP, 20)}, pixel)
    (v,) = call(server, "GET", f"/map/history?day={_day(1)}", headers=pixel)[1]["visits"]
    assert v["place"] == "Southland" and v["minutes"] == 35


def test_keep_days_prunes_and_forget(server, state):
    pixel, _ = _device("Pixel 8a")
    old = _stay(_noon(6), HOME, 10)
    call(server, "POST", "/device/location/history", {"points": old + _stay(_noon(1), HOME, 10)}, pixel)
    got = call(server, "GET", "/map/history/settings", headers=pixel)[1]
    assert got["keep_days"] == 90 and got["kept"]["points"] == len(old) * 2
    for bad in ({"keep_days": 7}, {"keep_days": True}, {}):
        assert call(server, "POST", "/map/history/settings", bad, pixel)[0].status == 400
    assert call(server, "POST", "/map/history/settings", {"keep_days": None}, pixel)[1]["keep_days"] is None
    assert call(server, "POST", "/map/history/settings", {"keep_days": 30}, pixel)[1]["keep_days"] == 30

    # A short keep: what is older goes at once, and is never read back.
    conn = lh._open()
    conn.execute("INSERT OR REPLACE INTO settings (account, keep_days, forever) VALUES ('', 5, 0)")
    conn.commit()
    conn.close()
    assert call(server, "GET", f"/map/history?day={_day(6)}", headers=pixel)[1]["points"] == []
    lh._prune(lh._open(), time.time(), force=True)
    assert lh.get_settings("")[1]["kept"]["points"] == len(old)

    res, obj = call(server, "POST", "/map/history/forget", {"day": _day(1)}, pixel)
    assert res.status == 200 and obj["forgot"] == len(old)
    assert call(server, "GET", f"/map/history?day={_day(1)}", headers=pixel)[1]["visits"] == []
    call(server, "POST", "/device/location/history", {"points": _stay(_noon(2), HOME, 5)}, pixel)
    assert call(server, "POST", "/map/history/forget", {"all": True}, pixel)[1]["all"] is True
    assert lh.get_settings("")[1]["kept"]["points"] == 0
    assert call(server, "POST", "/map/history/forget", {"day": "Tuesday"}, pixel)[0].status == 400


def test_bad_points_old_points_and_the_gate(server, state):
    pixel, _ = _device("Pixel 8a")
    now = time.time()
    body = {"points": [{"lat": 95, "lon": 1, "at": now}, {"lat": 1, "lon": 1, "at": now - 30 * 86400},
                       {"lat": "x"}, {"lat": -37.8, "lon": 144.9, "at": now * 1000}]}
    assert call(server, "POST", "/device/location/history", body, pixel)[1]["kept"] == 1  # ms read as s
    assert call(server, "POST", "/device/location/history", {"nope": 1}, pixel)[0].status == 400
    assert call(server, "POST", "/device/location/history", {"points": []})[0].status == 401
    assert call(server, "POST", "/device/location/history", {"points": []}, HOST)[0].status == 401
    guest, gid = _device("Visitor tablet")
    assert devices.set_role(gid, "guest")
    assert call(server, "POST", "/device/location/history", {"points": []}, guest)[0].status == 403
    for path in ("/map/history?day=2026-11-20", "/map/history/visits", "/map/history/settings"):
        assert call(server, "GET", path, headers=guest)[0].status == 403
        assert call(server, "GET", path)[0].status == 401
    assert call(server, "GET", "/map/history?day=2026-11-20", headers=HOST)[0].status == 200
    assert call(server, "GET", "/map/history?day=yesterday", headers=HOST)[0].status == 400


def test_names_off_and_a_geocoder_down(state, monkeypatch):
    t = _noon(1)
    lh.add("", "d_x", {"points": _stay(t, SHOP, 10)})

    def down(la, lo):
        raise OSError("no route")

    assert lh.name_visits("", lookup=down) == 0
    assert lh.day("", _day(1))[1]["visits"][0]["place"] is None
    monkeypatch.setenv("MEDIA_LOCATION_NAMES", "0")
    assert lh.names_on() is False
