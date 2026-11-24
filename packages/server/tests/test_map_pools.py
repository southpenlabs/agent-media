"""Rock pools (map_pools.py, maps step 11): watched spots, and the day-before heads-up for
tomorrow's good low tides in the spot's own time, once a day, with a swell warning."""
from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

from agent_media_server import map_pools as mp

from test_contract import call, server, shelf, signed_in, typed  # noqa: F401
from test_mic import _device

MEL = ZoneInfo("Australia/Melbourne")
NOW = dt.datetime(2026, 11, 24, 17, 30, tzinfo=MEL).timestamp()


def _at(day: int, h: int, m: int) -> float:
    return dt.datetime(2026, 11, day, h, m, tzinfo=MEL).timestamp()


def _outdoors(pools):
    return lambda la, lo: (True, {"tz": "Australia/Melbourne", "pools": pools})


def test_watch_rename_and_remove(tmp_path):
    p = tmp_path / "p.json"
    ok, got = mp.change("", {"name": "Blairgowrie", "lat": -38.365, "lon": 144.77}, p)
    sid = got["spot"]["id"]
    # The same spot again is the one watched, renamed.
    assert mp.change("", {"name": "Blairgowrie back beach", "lat": -38.3652, "lon": 144.7701}, p)[1]["spot"]["id"] == sid
    assert [s["name"] for s in mp.listing("", p)[1]["spots"]] == ["Blairgowrie back beach"]
    assert mp.change("", {"name": "x", "lat": 91, "lon": 0}, p)[1]["code"] == "bad_point"
    assert mp.change("", {"name": " ", "lat": 1, "lon": 0}, p)[1]["code"] == "bad_name"
    assert mp.change("", {"id": sid, "delete": True}, p)[1] == {"deleted": sid}
    assert mp.change("", {"id": sid, "delete": True}, p)[1]["status"] == 404


def test_tomorrows_good_lows_are_told_once_with_the_swell(tmp_path):
    p = tmp_path / "p.json"
    mp.change("", {"name": "Blairgowrie", "lat": -38.365, "lon": 144.77}, p)
    mp.change("acct-other", {"name": "Elsewhere", "lat": -38.4, "lon": 144.9}, p)
    pools = [{"at": _at(24, 18, 0), "m": -1.3, "good": True},                  # today: not tomorrow
             {"at": _at(25, 6, 42), "m": -0.38, "good": False},                # not good
             {"at": _at(25, 17, 40), "m": -1.19, "good": True, "swell_m": 2.7},
             {"at": _at(26, 18, 32), "m": -1.34, "good": True}]
    posted = []
    ok, got = mp.check(p, NOW, _outdoors(pools), posted.append)
    assert ok and [(t["name"], t["m"]) for t in got["told"]] == [("Blairgowrie", -1.19)]
    assert len(posted) == 1
    note = posted[0]
    assert note["id"].startswith("pools-") and note["title"] == "Rock pools at Blairgowrie tomorrow"
    assert note["text"] == "Low −1.19 m at 5:40 pm. Swell 2.7 m then: keep off the rock platforms."
    # Run again the same evening: nothing new.
    assert mp.check(p, NOW + 600, _outdoors(pools), posted.append)[1]["told"] == [] and len(posted) == 1
    # Nothing good tomorrow: nothing said.
    assert mp.check(p, NOW + 86400, _outdoors(pools[:3]), posted.append)[1]["told"] == []


def test_routes(server, monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    pixel, _ = _device("Pixel 8a")
    res, obj = call(server, "POST", "/map/pools", {"name": "Blairgowrie", "lat": -38.365, "lon": 144.77}, headers=pixel)
    assert res.status == 200 and obj["spot"]["name"] == "Blairgowrie"
    assert [s["name"] for s in call(server, "GET", "/map/pools", headers=pixel)[1]["spots"]] == ["Blairgowrie"]
    assert call(server, "POST", "/map/pools/check", {}, headers=pixel)[0].status == 401
