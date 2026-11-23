"""GET/POST /map/things (map_things.py): where a person's devices, rooms and
speakers are, and the Map's "Your things" layer (maps proposal step 4)."""
from __future__ import annotations

import pytest

from agent_media_server import devices, map_things

from test_contract import call, server, shelf, signed_in, typed  # noqa: F401
from test_mic import _device

ROOMS = [{"id": "kitchen-music", "name": "Kitchen", "state": {"connected": True, "on": True, "volume": 40, "playing": True}},
         {"id": "study-music", "name": "Study", "state": {"connected": False, "on": False, "volume": 70, "playing": False}}]
SPEAKERS = [{"id": "local", "name": "red2", "state": {"speech": False, "music": False}},
            {"id": "phone", "name": "Phone (Termux player)", "state": {"speech": True, "music": False}}]


@pytest.fixture()
def fake(monkeypatch, tmp_path):
    """The rooms and speakers faked (no Snapcast, no players); devices are the real store's."""
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    rooms: list = [list(ROOMS)]
    real = map_things._sources

    def sources():
        got = real()
        got["room"] = lambda: rooms[0]
        got["speaker"] = lambda: SPEAKERS
        return got

    monkeypatch.setattr(map_things, "_sources", sources)
    return rooms


def test_place_list_move_and_remove(server, fake):
    pixel, pid = _device("Pixel 8a")
    assert devices.set_kind(pid, "phone")
    got = call(server, "GET", "/map/things", headers=pixel)[1]
    assert got["things"] == []
    unplaced = {(u["kind"], u["id"]): u["name"] for u in got["unplaced"]}
    assert unplaced[("device", pid)] == "Pixel 8a"
    assert unplaced[("room", "kitchen-music")] == "Kitchen"
    assert unplaced[("speaker", "local")] == "red2"

    res, obj = call(server, "POST", "/map/things", {"kind": "room", "id": "kitchen-music",
                                                     "lat": -37.8, "lon": 144.96, "label": "Kitchen speaker"}, headers=pixel)
    assert res.status == 200 and obj["thing"]["name"] == "Kitchen" and obj["thing"]["label"] == "Kitchen speaker"
    call(server, "POST", "/map/things", {"kind": "device", "id": pid, "lat": -37.81, "lon": 144.97}, headers=pixel)
    got = call(server, "GET", "/map/things", headers=pixel)[1]
    things = {t["id"]: t for t in got["things"]}
    assert things["kitchen-music"]["state"]["volume"] == 40
    assert things[pid]["state"]["online"] is True  # it just asked
    assert ("room", "kitchen-music") not in {(u["kind"], u["id"]) for u in got["unplaced"]}

    # Moved: replaced, the label kept.
    call(server, "POST", "/map/things", {"kind": "room", "id": "kitchen-music", "lat": -37.7, "lon": 144.9}, headers=pixel)
    got = call(server, "GET", "/map/things", headers=pixel)[1]
    kitchen = [t for t in got["things"] if t["id"] == "kitchen-music"]
    assert len(kitchen) == 1 and kitchen[0]["lat"] == -37.7 and kitchen[0]["label"] == "Kitchen speaker"

    # The layer: listed second, a Point per thing.
    ids = [lay["id"] for lay in call(server, "GET", "/map/layers", headers=pixel)[1]["layers"]]
    assert ids[:2] == ["phone", "things"]
    feats = call(server, "GET", "/map/layer?id=things", headers=pixel)[1]["features"]
    room = next(f for f in feats if f["properties"]["thing"] == "kitchen-music")
    assert room["geometry"]["coordinates"] == [144.9, -37.7]
    assert room["properties"]["icon"] == "room" and room["properties"]["title"] == "Kitchen speaker"
    assert room["properties"]["detail"] == "On, 40%"

    res, obj = call(server, "POST", "/map/things", {"kind": "room", "id": "kitchen-music", "delete": True}, headers=pixel)
    assert res.status == 200 and obj["deleted"] == {"kind": "room", "id": "kitchen-music"}
    res, obj = call(server, "POST", "/map/things", {"kind": "room", "id": "kitchen-music", "delete": True}, headers=pixel)
    assert res.status == 404 and obj["code"] == "not_placed"


def test_unknown_ids_bad_bodies_and_the_gate(server, fake):
    pixel, _ = _device("Pixel 8a")
    for body, status, code in (({"kind": "fridge", "id": "x", "lat": 1, "lon": 1}, 400, "bad_kind"),
                               ({"kind": "room", "id": "kitchen-music", "lat": 95, "lon": 1}, 400, "bad_point"),
                               ({"kind": "room", "id": "attic", "lat": 1, "lon": 1}, 404, "unknown"),
                               ({"kind": "speaker", "id": "sasonica", "lat": 1, "lon": 1}, 404, "unknown"),
                               ({"kind": "device", "id": "d_nope", "lat": 1, "lon": 1}, 404, "unknown")):
        res, obj = call(server, "POST", "/map/things", body, headers=pixel)
        assert res.status == status and obj["code"] == code, body
    hdrs, dev_id = _device("Visitor tablet")
    assert devices.set_role(dev_id, "guest")
    assert call(server, "GET", "/map/things", headers=hdrs)[0].status == 403
    assert call(server, "GET", "/map/things")[0].status == 401


def test_rooms_out_of_reach(server, fake):
    pixel, _ = _device("Pixel 8a")
    call(server, "POST", "/map/things", {"kind": "room", "id": "kitchen-music", "lat": -37.8, "lon": 144.96}, headers=pixel)
    fake[0] = None
    got = call(server, "GET", "/map/things", headers=pixel)[1]
    assert got["rooms_unreachable"] is True
    (kitchen,) = [t for t in got["things"] if t["kind"] == "room"]
    assert kitchen["name"] == "Kitchen" and "state" not in kitchen  # the name kept from placing
    assert not [u for u in got["unplaced"] if u["kind"] == "room"]
    # A placed one may still move; a new one waits.
    assert call(server, "POST", "/map/things", {"kind": "room", "id": "kitchen-music", "lat": -37.7, "lon": 144.9},
                headers=pixel)[0].status == 200
    res, obj = call(server, "POST", "/map/things", {"kind": "room", "id": "study-music", "lat": 1, "lon": 1}, headers=pixel)
    assert res.status == 503 and obj["code"] == "rooms_unreachable"


def test_an_unpaired_device_leaves_the_map(server, fake):
    pixel, _ = _device("Pixel 8a")
    _tab, tab_id = _device("Tablet")
    call(server, "POST", "/map/things", {"kind": "device", "id": tab_id, "lat": 1, "lon": 1}, headers=pixel)
    assert [t["id"] for t in call(server, "GET", "/map/things", headers=pixel)[1]["things"]] == [tab_id]
    assert devices.revoke(tab_id)
    assert call(server, "GET", "/map/things", headers=pixel)[1]["things"] == []


def test_a_device_of_no_kind_is_not_offered_but_may_be_placed(server, fake):
    pixel, pid = _device("Pixel 8a")
    _jobs, jobs_id = _device("red2 jobs")
    rows = map_things._devices("")
    assert next(r for r in rows if r["id"] == jobs_id).get("hidden") is True
    got = call(server, "GET", "/map/things", headers=pixel)[1]
    assert jobs_id not in {u["id"] for u in got["unplaced"]}
    assert call(server, "POST", "/map/things", {"kind": "device", "id": jobs_id, "lat": 1, "lon": 1},
                headers=pixel)[0].status == 200
    assert jobs_id in {t["id"] for t in call(server, "GET", "/map/things", headers=pixel)[1]["things"]}


def test_the_owners_things_are_shared_by_their_devices(server, fake):
    pixel, _ = _device("Pixel 8a")
    tablet, _ = _device("Tablet")
    call(server, "POST", "/map/things", {"kind": "speaker", "id": "local", "lat": 1, "lon": 2}, headers=pixel)
    got = call(server, "GET", "/map/things", headers=tablet)[1]["things"]
    assert [(t["kind"], t["id"], t["name"]) for t in got] == [("speaker", "local", "red2")]


# --- found beside the phone (docs/proposals/2026-11-22-things-place-themselves.md) ---

def test_network_says_where_only_when_it_can():
    assert map_things.network("203.0.113.7") == "203.0.113.7"
    assert map_things.network("192.168.1.20") == map_things.network("192.168.1.99") == "192.168.1.0/24"
    assert map_things.network("2001:db8:1:2::5") == map_things.network("2001:db8:1:2:ffff::1")
    for nothing in ("127.0.0.1", "100.101.1.2", "fd7a:115c:a1e0::5", "fe80::1", "", "not an ip"):
        assert map_things.network(nothing) is None, nothing


def _beside(fake, tmp_path):
    """A phone and a TV of one account, both last seen from 203.0.113.7."""
    pixel, pid = _device("Pixel 8a")
    tv, tid = _device("TCL Smart TV")
    assert devices.set_kind(pid, "phone") and devices.set_kind(tid, "tv")
    rows = devices.list_devices()
    for d in rows:
        d["last_ip"] = "203.0.113.7"
    return pixel, pid, tv, tid, rows


def test_a_device_on_the_phones_network_takes_its_fix(server, fake, tmp_path):
    pixel, pid, tv, tid, rows = _beside(fake, tmp_path)
    fix = {"lat": -37.812341, "lon": 144.962341, "accuracy_m": 12}
    bearer = pixel["Authorization"].removeprefix("Bearer ")
    assert map_things.on_location(bearer, fix, ip="203.0.113.7", devs=rows) == [tid]

    got = call(server, "GET", "/map/things", headers=pixel)[1]
    found = {t["id"]: t for t in got["things"]}
    assert set(found) == {tid}  # never the phone itself
    assert found[tid]["source"] == "phone" and found[tid]["accuracy_m"] == map_things.NET_M
    assert (found[tid]["lat"], found[tid]["lon"]) == (-37.81234, 144.96234)
    assert tid not in {u["id"] for u in got["unplaced"]}
    from agent_media_server import auth

    layer = {f["properties"]["thing"]: f["properties"] for f in map_things.features(auth.device_account(rows[0]))}
    assert layer[tid]["source"] == "phone" and layer[tid]["found_at"]

    # Another network, the tailnet, or a stale sighting: nothing found.
    assert map_things.on_location(bearer, fix, ip="198.51.100.1", devs=rows) == []
    assert map_things.on_location(bearer, fix, ip="100.90.1.1", devs=rows) == []
    for d in rows:
        d["last_seen"] = 0
    assert map_things.on_location(bearer, fix, ip="203.0.113.7", devs=rows) == []


def test_by_hand_wins_and_off_stays_off(server, fake, tmp_path):
    pixel, pid, tv, tid, rows = _beside(fake, tmp_path)
    bearer = pixel["Authorization"].removeprefix("Bearer ")
    fix = {"lat": -37.8, "lon": 144.9}
    map_things.on_location(bearer, fix, ip="203.0.113.7", devs=rows)

    # Placed by hand: that point, and a later fix does not move it.
    res, obj = call(server, "POST", "/map/things", {"kind": "device", "id": tid, "lat": -37.7, "lon": 145.0}, headers=pixel)
    assert res.status == 200 and obj["thing"]["source"] == "hand"
    assert map_things.on_location(bearer, {"lat": -38.0, "lon": 144.0}, ip="203.0.113.7", devs=rows) == []
    t = next(t for t in call(server, "GET", "/map/things", headers=pixel)[1]["things"] if t["id"] == tid)
    assert (t["lat"], t["source"]) == (-37.7, "hand")

    # Taken off: gone, and not found again until placed by hand.
    assert call(server, "POST", "/map/things", {"kind": "device", "id": tid, "delete": True}, headers=pixel)[0].status == 200
    assert map_things.on_location(bearer, fix, ip="203.0.113.7", devs=rows) == []
    got = call(server, "GET", "/map/things", headers=pixel)[1]
    assert tid not in {t["id"] for t in got["things"]} and tid in {u["id"] for u in got["unplaced"]}


def test_found_points_go_with_forget_all(server, fake, tmp_path):
    from agent_media_server import auth, location_history

    pixel, pid, tv, tid, rows = _beside(fake, tmp_path)
    bearer = pixel["Authorization"].removeprefix("Bearer ")
    map_things.on_location(bearer, {"lat": -37.8, "lon": 144.9}, ip="203.0.113.7", devs=rows)
    account = auth.device_account(rows[0])
    assert tid in map_things._found(account)
    assert location_history.forget(account, {"all": True})[0]
    assert map_things._found(account) == {}
