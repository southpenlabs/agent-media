"""Where I parked (map_parked.py, maps step 10): the car's spot, set by the phone as it
leaves the car's Bluetooth or by hand, taken off on reconnecting, drawn as a layer."""
from __future__ import annotations

import time

from agent_media_server import map_parked as mp

from test_contract import call, server, shelf, signed_in, typed  # noqa: F401
from test_mic import _device


def test_set_read_and_clear(tmp_path):
    p = tmp_path / "p.json"
    now = time.time()
    assert mp.get("", p, now)[1] == {"parked": None}
    ok, out = mp.change("", {"lat": -37.9, "lon": 145.0, "accuracy_m": 12.4, "at": now - 120,
                             "how": "bluetooth", "car": "  Corolla  "}, p, now)
    assert ok and out["parked"] == {"lat": -37.9, "lon": 145.0, "at": now - 120, "how": "bluetooth",
                                    "accuracy_m": 12, "car": "Corolla", "age_s": 120}
    f = mp.features("", p, now)
    assert f[0]["properties"]["title"] == "Your car" and f[0]["properties"]["detail"] == "Corolla"
    assert f[0]["geometry"]["coordinates"] == [145.0, -37.9]
    assert mp.features("acct-x", p, now) == []
    assert mp.change("", {"clear": True, "how": "bluetooth"}, p, now)[1] == {"cleared": True}
    assert mp.get("", p, now)[1]["parked"] is None


def test_a_spot_by_hand_outlives_a_bluetooth_clear_and_old_ones_go(tmp_path):
    p = tmp_path / "p.json"
    now = time.time()
    mp.change("", {"lat": 1, "lon": 2}, p, now)
    assert mp.change("", {"clear": True, "how": "bluetooth"}, p, now)[1] == {"cleared": False}
    assert mp.get("", p, now)[1]["parked"]["how"] == "hand"
    assert mp.get("", p, now + mp.MAX_AGE_S + 1)[1]["parked"] is None
    assert mp.change("", {"clear": True}, p, now)[1] == {"cleared": True}


def test_bad_input(tmp_path):
    p = tmp_path / "p.json"
    now = time.time()
    assert mp.change("", {"lat": 91, "lon": 0}, p, now)[1]["code"] == "bad_point"
    assert mp.change("", {"lat": "1", "lon": 0}, p, now)[1]["code"] == "bad_point"
    assert mp.change("", {"lat": 1, "lon": 0, "at": now - 2 * 86400}, p, now)[1]["code"] == "bad_at"


def test_route_and_layer(server, monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    pixel, _ = _device("Pixel 8a")
    res, obj = call(server, "POST", "/map/parked", {"lat": -37.9, "lon": 145.0, "how": "bluetooth"}, headers=pixel)
    assert res.status == 200 and obj["parked"]["how"] == "bluetooth"
    assert call(server, "GET", "/map/parked", headers=pixel)[1]["parked"]["lat"] == -37.9
    ids = [l["id"] for l in call(server, "GET", "/map/layers", headers=pixel)[1]["layers"]]
    assert "parked" in ids
    lay = call(server, "GET", "/map/layer?id=parked", headers=pixel)[1]
    assert [f["properties"]["title"] for f in lay["features"]] == ["Your car"]
    assert call(server, "GET", "/map/parked")[0].status == 401
