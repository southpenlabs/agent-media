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
