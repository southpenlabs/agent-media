"""`GET /map/layers` and `GET /map/layer`: the Map page's layers
(map_layers.py, §6.50; docs/proposals/2026-11-06-maps.md)."""

from __future__ import annotations

import time

from agent_media_server import devices, map_layers

from test_contract import call, server, shelf, signed_in, typed  # noqa: F401
from test_mic import _device


def test_the_phone_layer_shows_the_shared_fix(server):
    pixel, _ = _device("Pixel 8a")
    status = call(server, "GET", "/map/layers", headers=pixel)
    assert status[0].status == 200
    ids = [lay["id"] for lay in status[1]["layers"]]
    assert ids[0] == "phone"

    got = call(server, "GET", "/map/layer?id=phone", headers=pixel)[1]
    assert got["type"] == "FeatureCollection" and got["features"] == []

    call(server, "POST", "/device/location",
         {"lat": -37.8136, "lon": 144.9631, "accuracy_m": 12, "at": time.time() - 30}, pixel)
    got = call(server, "GET", "/map/layer?id=phone", headers=pixel)[1]
    (f,) = got["features"]
    assert f["geometry"] == {"type": "Point", "coordinates": [144.9631, -37.8136]}
    p = f["properties"]
    assert p["title"] == "Pixel 8a" and p["accuracy_m"] == 12 and 25 <= p["age_s"] <= 40

    # A box elsewhere leaves it out; a box round Melbourne keeps it.
    far = call(server, "GET", "/map/layer?id=phone&bbox=0,0,10,10", headers=pixel)[1]
    assert far["features"] == []
    near = call(server, "GET", "/map/layer?id=phone&bbox=144,-38,146,-37", headers=pixel)[1]
    assert len(near["features"]) == 1
    call(server, "POST", "/device/location", {"off": True}, pixel)


def test_unknown_layer_and_no_bearer(server):
    pixel, _ = _device("Pixel 8a")
    assert call(server, "GET", "/map/layer?id=nope", headers=pixel)[0].status == 404
    assert call(server, "GET", "/map/layers")[0].status == 401


def test_a_guest_does_not_see_the_map(server):
    hdrs, dev_id = _device("Visitor tablet")
    assert devices.set_role(dev_id, "guest")
    res, obj = call(server, "GET", "/map/layers", headers=hdrs)
    assert res.status == 403 and obj.get("code") == "guest"


def test_an_add_on_layer_and_a_failing_one(server, monkeypatch):
    pixel, _ = _device("Pixel 8a")
    cams = {"layer": {"id": "cams", "name": "Cameras", "kind": "points", "default_on": False},
            "features": lambda bbox: [{"type": "Feature", "properties": {"title": "Cam"},
                                       "geometry": {"type": "Point", "coordinates": [145, -37.9]}}]}
    broken = {"layer": {"id": "broken", "name": "Broken", "kind": "points"},
              "features": lambda bbox: 1 / 0}
    monkeypatch.setattr(map_layers, "_addons", lambda: [cams, broken])
    ids = [lay["id"] for lay in call(server, "GET", "/map/layers", headers=pixel)[1]["layers"]]
    assert ids == ["phone", "cams", "broken"]
    got = call(server, "GET", "/map/layer?id=cams", headers=pixel)[1]
    assert got["features"][0]["properties"]["title"] == "Cam"
    assert call(server, "GET", "/map/layer?id=broken", headers=pixel)[0].status == 502
