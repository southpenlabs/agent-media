"""Sharing a trip (map_trips.py): started while guiding, moved on, seen by whoever has the link, gone when it should be."""
from __future__ import annotations

from agent_media_server import map_trips

from test_contract import call, server, shelf, signed_in, typed  # noqa: F401
from test_mic import _device

TO = {"name": "Brunetti", "lat": -37.7981, "lon": 144.9687}


def test_a_trip_shared_moved_on_seen_publicly_and_ended(server, monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    pixel, _ = _device("Pixel 8a")
    res, obj = call(server, "POST", "/map/trips", {"to": TO, "mode": "walk", "at": {"lat": -37.81, "lon": 144.96},
                                                   "eta_at": 1000.5, "left_m": 900}, headers=pixel)
    assert res.status == 200
    trip = obj["trip"]
    assert trip["until"] == "trip" and len(trip["token"]) >= 30
    # Public: no bearer, the token is the key.
    res, seen = call(server, "GET", f"/map/trip?token={trip['token']}")
    assert res.status == 200 and seen["state"] == "going" and seen["to"] == TO and seen["left_m"] == 900
    assert seen["mode"] == "walk" and "owner" not in seen and "token" not in seen
    assert call(server, "GET", "/map/trip?token=nope")[0].status == 404
    # Moved on by the same device only.
    assert call(server, "POST", "/map/trips/update", {"id": trip["id"], "at": {"lat": -37.80, "lon": 144.965}, "left_m": 120},
                headers=pixel)[0].status == 200
    assert call(server, "GET", f"/map/trip?token={trip['token']}")[1]["left_m"] == 120
    other, _ = _device("Someone else")
    assert call(server, "POST", "/map/trips/update", {"id": trip["id"], "left_m": 1}, headers=other)[0].status == 404
    # Ended on arrival: with until=trip, gone at once.
    res, obj = call(server, "POST", "/map/trips/end", {"id": trip["id"], "arrived": True}, headers=pixel)
    assert obj["gone"] is True
    assert call(server, "GET", f"/map/trip?token={trip['token']}")[0].status == 404


def test_bad_bodies_and_no_bearer(server, monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    pixel, _ = _device("Pixel 8a")
    assert call(server, "POST", "/map/trips", {"to": TO})[0].status == 401
    assert call(server, "POST", "/map/trips", {"to": {"lat": 99}}, headers=pixel)[1]["code"] == "bad_point"
    assert call(server, "POST", "/map/trips", {"to": TO, "until": "forever"}, headers=pixel)[1]["code"] == "bad_until"


def test_how_long_a_link_lasts(tmp_path):
    p = tmp_path / "trips.json"

    def started(until):
        return map_trips.start("d", {"to": TO, "until": until}, path=p, now=1000.0)[1]["trip"]

    def seen(t, now):
        return map_trips.public(t["token"], path=p, now=now)

    t15 = started("15m")
    map_trips.end("d", {"id": t15["id"], "arrived": True}, path=p, now=2000.0)
    assert seen(t15, 2000.0 + 899)[1]["state"] == "arrived"
    assert seen(t15, 2000.0 + 901)[0] is False
    t1h = started("1h")
    assert seen(t1h, 1000.0 + 3599)[0] and not seen(t1h, 1000.0 + 3601)[0]
    ts = started("stop")
    map_trips.end("d", {"id": ts["id"]}, path=p, now=1500.0)
    assert seen(ts, 5000.0)[1]["state"] == "ended"
    map_trips.end("d", {"id": ts["id"], "stop": True}, path=p, now=6000.0)
    assert not seen(ts, 6000.5)[0]
    # Whatever until says, gone a day after it started.
    tall = started("stop")
    assert seen(tall, 1000.0 + map_trips.MAX_S - 1)[0] and not seen(tall, 1000.0 + map_trips.MAX_S + 1)[0]
