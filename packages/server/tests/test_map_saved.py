"""GET/POST /map/saved (map_saved.py): a person's Home, Work and other saved places."""
from __future__ import annotations

from agent_media_server import devices

from test_contract import call, server, shelf, signed_in, typed  # noqa: F401
from test_mic import _device


def test_home_work_and_own_places_set_listed_and_removed(server, monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    pixel, _ = _device("Pixel 8a")
    assert call(server, "GET", "/map/saved", headers=pixel)[1]["places"] == []
    res, obj = call(server, "POST", "/map/saved", {"id": "work", "name": "Office", "lat": -37.81, "lon": 144.96}, headers=pixel)
    assert res.status == 200 and obj["place"] == {"id": "work", "name": "Office", "lat": -37.81, "lon": 144.96}
    call(server, "POST", "/map/saved", {"name": "Gym", "label": "Gym", "lat": -37.8, "lon": 144.9}, headers=pixel)
    call(server, "POST", "/map/saved", {"id": "home", "name": "12 Smith St", "address": "12 Smith St, Fitzroy",
                                        "lat": -37.79, "lon": 144.98}, headers=pixel)
    got = call(server, "GET", "/map/saved", headers=pixel)[1]["places"]
    assert [p["id"] for p in got][:2] == ["home", "work"] and got[2]["id"].startswith("saved-")
    assert got[0]["address"] == "12 Smith St, Fitzroy" and got[2]["label"] == "Gym"
    # Home moved: replaced, not added.
    call(server, "POST", "/map/saved", {"id": "home", "name": "New place", "lat": -37.7, "lon": 144.9}, headers=pixel)
    got = call(server, "GET", "/map/saved", headers=pixel)[1]["places"]
    assert len(got) == 3 and got[0]["name"] == "New place" and "address" not in got[0]
    # Another device of the same owner sees them.
    tablet, _ = _device("Tablet")
    assert [p["id"] for p in call(server, "GET", "/map/saved", headers=tablet)[1]["places"]][:2] == ["home", "work"]
    assert call(server, "POST", "/map/saved", {"id": "work", "delete": True}, headers=pixel)[1]["deleted"] == "work"
    res, obj = call(server, "POST", "/map/saved", {"id": "work", "delete": True}, headers=pixel)
    assert res.status == 404 and obj["code"] == "not_saved"


def test_bad_bodies_guest_and_no_bearer(server, monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    pixel, _ = _device("Pixel 8a")
    for body, code in (({"id": "home", "name": "x", "lat": 95, "lon": 0}, "bad_point"),
                       ({"id": "home", "name": "x", "lat": "a", "lon": 0}, "bad_point"),
                       ({"id": "home", "name": " ", "lat": 1, "lon": 1}, "bad_name"),
                       ({"id": "Not OK!", "name": "x", "lat": 1, "lon": 1}, "bad_id")):
        res, obj = call(server, "POST", "/map/saved", body, headers=pixel)
        assert res.status == 400 and obj["code"] == code, body
    hdrs, dev_id = _device("Visitor tablet")
    assert devices.set_role(dev_id, "guest")
    assert call(server, "GET", "/map/saved", headers=hdrs)[0].status == 403
    assert call(server, "GET", "/map/saved")[0].status == 401
