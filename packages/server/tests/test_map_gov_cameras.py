"""Other states' official camera sites (map_gov_cameras.py, §6.50): read from
each state's open list, a layer each where switched on, and in the alerts."""

from __future__ import annotations

from agent_media_server import map_gov_cameras as gc

from test_contract import call, server, shelf, signed_in, typed  # noqa: F401
from test_mic import _device

ACT_SPEED = """CAMERA TYPE,CAMERA LOCATION CODE,LOCATION_CODE,LATITUDE,LONGITUDE,LOCATION DESCRIPTION,Location,Decommissioned Camera_Date
RED LIGHT AND SPEED CAMERA,1025,1025,-35.1863,149.1191,GUNGAHLIN DRIVE/GUNDAROO DRIVE,"(-35.1863, 149.1191)",
MOBILE SPEED CAMERA,3062,3062,-35.355139,149.053829,Badimara Street Waramanga,"(-35.355139, 149.053829)",
FIXED ONLY SPEED CAMERA,1100,1100,-35.30,149.13,CLUNIES ROSS STREET,"(-35.30, 149.13)",01/02/2024
POINT TO POINT CAMERA,1200,1200,-35.25,149.10,BARTON HIGHWAY,"(-35.25, 149.10)",
"""
ACT_PHONE = """Site Name,Suburb,Latitude,Longitude,Direction of travel to be monitored,Site Type,Location Code
"Woodcock Drive, Gordon",Gordon,-35.4502731,149.0827983,E,Transportable Site,7071
"""
NSW_FIXED = """SZ?,SUBURB/TOWN,ROAD/S,Cameras,Lat(1),Long(1),Lat(2),Long(2)
Y,Bankstown,"Hume Highway, between Rookwood Road and Stacey Street (school zone)",2,-33.90591876,151.0385979,-33.90618282,151.0389152
"""
NSW_RED = """SZ?,SUBURB/TOWN,ROAD/S,Cameras,Lat(1),Long(1),Lat(2),Long(2),Lat(3),Long(3),
N,Ashfield,Hume Highway and Fredrick Street,1,-33.888045,151.117876,,,,,
"""
TEXTS = {gc.ACT_SPEED: ACT_SPEED, gc.ACT_PHONE: ACT_PHONE, gc.NSW_FIXED: NSW_FIXED, gc.NSW_RED: NSW_RED}


def test_each_states_list_read():
    act = gc.parse_act(TEXTS)
    # The decommissioned camera is left out; each kind named as the alerts know it.
    assert [f["properties"]["enforces"] for f in act] == ["red_light", "mobile_speed", "average_speed", "phone"]
    assert act[0]["geometry"]["coordinates"] == [149.1191, -35.1863]
    assert act[0]["properties"]["detail"] == "Gungahlin Drive/Gundaroo Drive"
    assert act[3]["properties"]["detail"] == "Woodcock Drive, Gordon (facing traffic going E)"
    nsw = gc.parse_nsw(TEXTS)
    assert [f["properties"]["enforces"] for f in nsw] == ["speed", "speed", "red_light"]
    assert nsw[0]["properties"]["detail"].endswith("(school zone), Bankstown")


def test_off_unless_switched_on_then_a_layer_and_alerts(server, monkeypatch):
    pixel, _ = _device("Pixel 8a")
    ids = [lay["id"] for lay in call(server, "GET", "/map/layers", headers=pixel)[1]["layers"]]
    assert "cameras_act" not in ids and "cameras_nsw" not in ids
    monkeypatch.setenv("MEDIA_MAP_CAMERAS_ACT", "1")
    monkeypatch.setattr(gc, "_get", lambda url: TEXTS[url])
    assert gc.refresh("act") == {"state": "act", "sites": 4}
    lays = {lay["id"]: lay for lay in call(server, "GET", "/map/layers", headers=pixel)[1]["layers"]}
    assert "cameras_act" in lays and "cameras_nsw" not in lays and "CC BY 4.0" in lays["cameras_act"]["source"]
    got = call(server, "GET", "/map/layer?id=cameras_act&bbox=149.0,-35.5,149.2,-35.1", headers=pixel)[1]
    assert len(got["features"]) == 4 and got["as_of"] > 0
    # The alerts: the cameras, not the vans' possible sites.
    al = call(server, "GET", "/map/alerts?near=-35.25,149.10&radius_km=30", headers=pixel)[1]
    kinds = sorted(p["kind"] for p in al["points"] if p.get("source") == "act")
    assert kinds == ["average_speed", "phone", "red_light"]


def test_a_camera_osm_already_has_is_not_alerted_twice(monkeypatch):
    monkeypatch.setenv("MEDIA_MAP_CAMERAS_ACT", "1")
    monkeypatch.setattr(gc, "_get", lambda url: TEXTS[url])
    gc.refresh("act")
    near = [{"lat": -35.18632, "lon": 149.11912, "kind": "red_light"}]
    got = gc.alert_points((149.0, -35.5, 149.2, -35.1), near)
    assert sorted(p["kind"] for p in got) == ["average_speed", "phone"]
