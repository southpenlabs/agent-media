"""Sharing a trip (map_trips.py): started while guiding, moved on, seen by whoever has the link, gone when it should be."""
from __future__ import annotations

import time

from agent_media_server import map_trips

from test_contract import call, server, shelf, signed_in, typed  # noqa: F401
from test_mic import _device

TO = {"name": "Brunetti", "lat": -37.7981, "lon": 144.9687}


def test_a_trip_shared_moved_on_seen_publicly_and_ended(server, monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    pixel, _ = _device("Pixel 8a")
    res, obj = call(server, "POST", "/map/trips", {"to": TO, "mode": "walk", "at": {"lat": -37.81, "lon": 144.96},
                                                   "eta_at": time.time() + 600, "left_m": 900}, headers=pixel)
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


def test_a_trip_ended_at_a_time_that_rounds_up_is_gone_at_once(tmp_path):
    p = tmp_path / "trips.json"
    t = map_trips.start("d", {"to": TO}, path=p, now=1000.0)[1]["trip"]
    ok, obj = map_trips.end("d", {"id": t["id"], "arrived": True}, path=p, now=2000.0006)
    assert ok and obj["gone"] is True
    assert not map_trips.public(t["token"], path=p, now=2000.0006)[0]


class _Room:
    """A Matrix room as map_trips.send sees one: its members and send."""

    def __init__(self, members=None, ok=True):
        self.members = members or {}
        self.sent: list[str] = []
        self.ok = ok

    def send(self, text, reply_to=""):
        self.sent.append(text)
        return (True, {"event_id": "$e"}) if self.ok else (False, {"status": 502, "error": "homeserver down"})


def test_sent_in_a_chat_then_a_heads_up_and_the_arrival(tmp_path, monkeypatch):
    monkeypatch.setenv("MATRIX_OWNER_ID", "@david:hs")
    p = tmp_path / "trips.json"
    room = _Room({"@david:hs": "David", "@kim:hs": "Kim"})
    rooms = {"room-kim": room}.get
    t = map_trips.start("d", {"to": TO, "eta_at": 1000.0 + 1800, "tz": "Australia/Melbourne",
                              "line": [[144.96, -37.81], [144.965, -37.805], [144.9687, -37.7981]]},
                        path=p, now=1000.0)[1]["trip"]
    # No public address, no link to send.
    assert map_trips.send("d", {"id": t["id"], "thread": "room-kim"}, path=p, now=1001.0, rooms=rooms,
                          base="")[1]["code"] == "no_public_address"
    assert map_trips.send("d", {"id": t["id"], "thread": "nope"}, path=p, now=1001.0, rooms=rooms,
                          base="https://x.example")[1]["code"] == "no_room"
    assert map_trips.send("other", {"id": t["id"], "thread": "room-kim"}, path=p, now=1001.0, rooms=rooms,
                          base="https://x.example")[1]["code"] == "no_trip"
    ok, obj = map_trips.send("d", {"id": t["id"], "thread": "room-kim"}, path=p, now=1001.0, rooms=rooms,
                             base="https://x.example")
    assert ok, obj
    # 2800 s after the epoch is 10:46 am in Melbourne (UTC+10 in January 1970).
    assert room.sent == [f"David is on the way to Brunetti, arriving about 10:46 am. Follow along: "
                         f"https://x.example/trip/{t['token']}"]
    assert room.sent[0].endswith(f"Follow along: https://x.example/trip/{t['token']}")
    seen = map_trips.public(t["token"], path=p, now=1002.0)[1]
    assert seen["name"] == "David" and seen["line"][-1] == [144.9687, -37.7981]
    told: list = []

    def tell(threads, text):
        told.append((threads, text))

    # Far off: nothing. Within five minutes: the heads-up, once.
    map_trips.update("d", {"id": t["id"], "eta_at": 1500.0 + 900}, path=p, now=1500.0, tell=tell)
    assert told == []
    map_trips.update("d", {"id": t["id"], "eta_at": 2000.0 + 240}, path=p, now=2000.0, tell=tell)
    map_trips.update("d", {"id": t["id"], "eta_at": 2060.0 + 180}, path=p, now=2060.0, tell=tell)
    assert told == [(["room-kim"], "David is about 4 minutes from Brunetti.")]
    # Arrived: told; ended by Stop instead it would not be.
    map_trips.end("d", {"id": t["id"], "arrived": True}, path=p, now=2240.0, tell=tell)
    assert told[-1] == (["room-kim"], "David has arrived at Brunetti.")
    assert len(told) == 2
    t2 = map_trips.start("d", {"to": TO}, path=p, now=3000.0)[1]["trip"]
    map_trips.send("d", {"id": t2["id"], "thread": "room-kim"}, path=p, now=3001.0, rooms=rooms, base="https://x.example")
    map_trips.end("d", {"id": t2["id"], "stop": True}, path=p, now=3100.0, tell=tell)
    assert len(told) == 2


def test_the_words_without_a_name_and_a_failed_send(tmp_path):
    p = tmp_path / "trips.json"
    t = map_trips.start("d", {"to": TO}, path=p, now=1000.0)[1]["trip"]
    down = _Room(ok=False)
    ok, obj = map_trips.send("d", {"id": t["id"], "thread": "r"}, path=p, now=1001.0, rooms={"r": down}.get,
                             base="https://x.example")
    assert not ok and obj["code"] == "not_sent" and obj["status"] == 502
    trip = {"to": {"name": "Brunetti"}, "eta_at": 1060.0}
    assert map_trips.words("start", trip, 1000.0, "https://l").startswith("On my way to Brunetti, arriving about ")
    assert map_trips.words("near", trip, 1000.0) == "About 1 minute away from Brunetti."
    assert map_trips.words("arrived", trip, 1000.0) == "Arrived at Brunetti."
    # A failing room leaves the others told.
    good = _Room()

    def rooms(th):
        if th == "bad":
            raise RuntimeError("boom")
        return good

    got = map_trips._tell(["bad", "good"], "hi", rooms=rooms)
    assert [ok for _, ok, _ in got] == [False, True] and good.sent == ["hi"]


def test_gone_an_hour_after_the_eta_and_the_line_thinned(tmp_path):
    p = tmp_path / "trips.json"
    line = [[144.9 + i * 1e-4, -37.8] for i in range(1000)]
    t = map_trips.start("d", {"to": TO, "eta_at": 2000.0, "line": line}, path=p, now=1000.0)[1]["trip"]
    seen = map_trips.public(t["token"], path=p, now=1001.0)[1]
    assert len(seen["line"]) == map_trips.LINE_MAX and seen["line"][0] == line[0] and seen["line"][-1] == line[-1]
    # The phone stopped sending: the link ends an hour after the last ETA.
    assert map_trips.public(t["token"], path=p, now=2000.0 + 3599)[0]
    assert not map_trips.public(t["token"], path=p, now=2000.0 + 3601)[0]
    assert map_trips.update("d", {"id": t["id"], "left_m": 5}, path=p, now=2000.0 + 3601)[1]["code"] == "no_trip"
    # Until I stop sharing is the person's own choice: not cut at the ETA.
    ts = map_trips.start("d", {"to": TO, "eta_at": 2000.0, "until": "stop"}, path=p, now=1000.0)[1]["trip"]
    assert map_trips.public(ts["token"], path=p, now=9000.0)[0]


def test_the_page_opens_without_a_login_and_refuses_a_gone_trip(server, monkeypatch, tmp_path):
    """/trip/<token> is the link sent: no bearer, never indexed, on to the app's
    page; the JSON beside it answers the trip, and 404 once it is gone."""
    import urllib.error
    import urllib.request

    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    pixel, _ = _device("Pixel 8a")
    trip = call(server, "POST", "/map/trips", {"to": TO, "eta_at": time.time() + 900, "name": "David"},
                headers=pixel)[1]["trip"]

    class NoFollow(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *a, **k):
            return None

    base = f"http://{server[0]}:{server[1]}"
    opener = urllib.request.build_opener(NoFollow)
    try:
        opener.open(f"{base}/trip/{trip['token']}")
        raise AssertionError("no redirect")
    except urllib.error.HTTPError as e:
        assert e.code == 302
        assert e.headers["Location"] == f"/app/trip/{trip['token']}"
        assert "noindex" in e.headers["X-Robots-Tag"]
    res, seen = call(server, "GET", f"/map/trip?token={trip['token']}")
    assert res.status == 200 and seen["name"] == "David" and seen["state"] == "going"
    call(server, "POST", "/map/trips/end", {"id": trip["id"], "arrived": True}, headers=pixel)
    res, seen = call(server, "GET", f"/map/trip?token={trip['token']}")
    assert res.status == 404 and seen["code"] == "no_trip"
