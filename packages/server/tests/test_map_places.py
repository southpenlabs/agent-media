"""`GET /map/places`: places of a kind near a point or in a box, from
OpenStreetMap through Overpass (map_places.py, §6.50). Overpass is faked
throughout (conftest refuses the real one)."""

from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

import pytest

from agent_media_server import devices, map_osm, map_places

from test_contract import call, server, shelf, signed_in, typed  # noqa: F401
from test_mic import _device

#: The real client, taken at import, before conftest swaps it for a refusal.
_REAL_OVERPASS = map_osm.overpass

MELB = ZoneInfo("Australia/Melbourne")
#: A Thursday mid-morning in Melbourne.
THU_10 = dt.datetime(2026, 11, 19, 10, 30, tzinfo=MELB)

ANSWER = {
    "osm3s": {"timestamp_osm_base": "2026-11-19T00:15:00Z"},
    "elements": [
        {"type": "node", "id": 1, "lat": -37.8140, "lon": 144.9635,
         "tags": {"amenity": "pharmacy", "name": "Near Chemist", "opening_hours": "Mo-Fr 09:00-17:00; PH off",
                  "addr:housenumber": "12", "addr:street": "Swanston Street", "addr:suburb": "Melbourne",
                  "phone": "+61 3 9000 0000", "website": "https://near.example", "dispensing": "yes",
                  "payment:cash": "yes"}},
        {"type": "way", "id": 2, "center": {"lat": -37.8180, "lon": 144.9660},
         "tags": {"healthcare": "pharmacy", "name": "Far Chemist", "opening_hours": "Sa-Su 10:00-14:00"}},
        {"type": "node", "id": 3, "lat": -37.8150, "lon": 144.9640,
         "tags": {"amenity": "pharmacy", "name": "No Hours"}},
        {"type": "node", "id": 4, "lat": -37.8145, "lon": 144.9637,
         "tags": {"amenity": "pharmacy", "brand": "Brandy", "opening_hours": "not hours at all!!"}},
        {"type": "node", "id": 5, "lat": -37.9500, "lon": 145.2000,
         "tags": {"amenity": "pharmacy", "name": "Out Of Range", "opening_hours": "24/7"}},
        {"type": "relation", "id": 6, "tags": {"amenity": "pharmacy", "name": "Nowhere"}},
    ],
}


@pytest.fixture
def overpass(monkeypatch):
    asked: list[str] = []

    def fake(query, timeout=None):
        asked.append(query)
        return ANSWER

    monkeypatch.setattr(map_osm, "overpass", fake)
    map_places.forget()
    return asked


def test_opening_hours_cases():
    lat, lon = -37.81, 144.96
    assert map_places.open_state("Mo-Fr 09:00-17:00", lat, lon, THU_10)[0] is True
    assert map_places.open_state("Mo-Fr 09:00-17:00", lat, lon, THU_10.replace(hour=18))[0] is False
    assert map_places.open_state("Mo-Fr 09:00-17:00", lat, lon,
                                 THU_10 + dt.timedelta(days=2))[0] is False  # Saturday
    # Several ranges in a day; 24/7; off; past midnight.
    two = "Mo-Su 08:00-12:00,13:00-17:00"
    assert map_places.open_state(two, lat, lon, THU_10.replace(hour=12, minute=30))[0] is False
    assert map_places.open_state(two, lat, lon, THU_10.replace(hour=14))[0] is True
    assert map_places.open_state("24/7", lat, lon, THU_10) == (True, None)
    assert map_places.open_state("Mo-Su off", lat, lon, THU_10)[0] is False
    assert map_places.open_state("Th 20:00-02:00", lat, lon,
                                 THU_10.replace(hour=23, minute=59) + dt.timedelta(hours=1))[0] is True
    # Victoria's public holidays come from the position: Christmas Day.
    xmas = dt.datetime(2026, 12, 25, 10, 0, tzinfo=MELB)
    assert map_places.open_state("Mo-Su 09:00-17:00; PH off", lat, lon, xmas)[0] is False
    # When it next changes, in the place's own time.
    is_open, changes = map_places.open_state("Mo-Fr 09:00-17:00", lat, lon, THU_10)
    assert changes == "2026-11-19T17:00:00+11:00"
    # Perth's hours are Perth's: 10:30 in Melbourne is 07:30 there.
    assert map_places.open_state("Mo-Fr 09:00-17:00", -31.95, 115.86, THU_10)[0] is False
    # Unknown, unreadable, and a UTC time.
    assert map_places.open_state("Mo-Fr 09:00-17:00 unknown", lat, lon, THU_10)[0] is None
    assert map_places.open_state("whenever we feel like it", lat, lon, THU_10) == (None, None)
    utc = THU_10.astimezone(dt.timezone.utc)
    assert map_places.open_state("Mo-Fr 09:00-17:00", lat, lon, utc)[0] is True


def test_kinds_and_their_aliases():
    assert map_places.kind_of("pharmacy") == "pharmacy"
    assert map_places.kind_of("Chemists") == "pharmacy"
    assert map_places.kind_of("petrol station") == "fuel"
    assert map_places.kind_of("EV-charger") == "ev_charger"
    assert map_places.kind_of("lookout") == "viewpoint"
    assert map_places.kind_of("toilet") == "toilets"
    assert map_places.kind_of("spaceport") is None
    ids = [k["id"] for k in map_places.kinds()]
    assert "drinking_water" in ids and len(ids) == len(set(ids))


def test_nearest_first_with_hours(overpass):
    ok, got = map_places.places(near="-37.8136,144.9631", kind="pharmacy", now=THU_10)
    assert ok, got
    assert got["kinds"] == ["pharmacy"] and got["radius_m"] == 1500
    names = [p["name"] for p in got["places"]]
    # Out of range and the relation with no centre are left out; the brand
    # names a place with no name.
    assert names == ["Near Chemist", "Brandy", "No Hours", "Far Chemist"]
    near = got["places"][0]
    assert near["id"] == "node/1" and near["open_now"] is True
    assert near["address"] == "12 Swanston Street, Melbourne"
    assert near["phone"] == "+61 3 9000 0000" and near["website"] == "https://near.example"
    assert near["opening_hours"] == "Mo-Fr 09:00-17:00; PH off"
    assert near["tags"] == {"dispensing": "yes"}
    assert 0 < near["distance_m"] < got["places"][1]["distance_m"]
    by = {p["name"]: p for p in got["places"]}
    assert by["Brandy"]["open_now"] is None and by["No Hours"]["open_now"] is None
    assert by["Far Chemist"]["open_now"] is False and by["Far Chemist"]["id"] == "way/2"
    assert got["source"].startswith("©") and got["as_of"] == 1795047300
    # The query is built from the kind's filters, around the rounded point.
    (q,) = overpass
    assert '["amenity"="pharmacy"](around:1620,-37.814,144.963)' in q
    assert '["healthcare"="pharmacy"]' in q and "out center tags" in q


def test_open_now_keeps_the_open_ones(overpass):
    ok, got = map_places.places(near="-37.8136,144.9631", kind="chemist", open_now="1",
                                radius_m="50000", now=THU_10)
    assert ok
    assert [p["name"] for p in got["places"]] == ["Near Chemist", "Out Of Range"]
    assert got["open_now"] is True and got["hours_unknown"] == 2


def test_a_box_and_a_limit(overpass):
    ok, got = map_places.places(bbox="144.96,-37.82,144.97,-37.81", kind="pharmacy", limit="2",
                                now=THU_10)
    assert ok and len(got["places"]) == 2 and "distance_m" not in got["places"][0]
    assert '(-37.82,144.96,-37.81,144.97)' in overpass[0]


def test_along_a_route_in_the_order_it_reaches_them(overpass):
    # David, 21 Nov 2026: "add search along route". North up Swanston Street from the south.
    route = "-37.8200,144.9660;-37.8150,144.9645;-37.8100,144.9630"
    ok, got = map_places.places(along=route, kind="pharmacy", now=THU_10)
    assert ok, got
    assert got["radius_m"] == 500 and got["along_points"] == 3
    names = [p["name"] for p in got["places"]]
    assert names == ["Far Chemist", "No Hours", "Brandy", "Near Chemist"]
    far, near = got["places"][0], got["places"][-1]
    assert far["ahead_m"] < near["ahead_m"]
    assert all(p["off_route_m"] <= 500 for p in got["places"])
    assert near["distance_m"] == near["ahead_m"] + near["off_route_m"]
    # Overpass is asked around the line itself, its points rounded.
    (q,) = overpass
    assert "(around:620,-37.82,144.966,-37.815,144.964,-37.81,144.963)" in q


def test_along_refuses_what_it_cannot_search(overpass):
    assert map_places.places(along="nonsense", kind="pharmacy")[1]["code"] == "bad_along"
    many = ";".join(f"{-37.0 - i * 0.001:.3f},144.96" for i in range(map_places.ALONG_MAX_POINTS + 1))
    assert map_places.places(along=many, kind="pharmacy")[1]["code"] == "bad_along"
    assert map_places.places(along="-37.81,144.96;-36.0,144.96", kind="pharmacy")[1]["code"] == "too_long"
    assert map_places.places(along="-37.81,144.96", near="-37.81,144.96", kind="pharmacy")[1]["code"] == "near_or_bbox"
    assert overpass == []


def test_answers_are_kept_a_few_minutes(overpass):
    clock = [1000.0]
    a = map_places.places(near="-37.8136,144.9631", kind="pharmacy", clock=lambda: clock[0])[1]
    # A few metres on is the same rounded point: nothing asked.
    b = map_places.places(near="-37.81362,144.96312", kind="pharmacy", clock=lambda: clock[0])[1]
    assert len(overpass) == 1 and a["cached"] is False and b["cached"] is True
    clock[0] += map_places.KEEP_S + 1
    map_places.places(near="-37.8136,144.9631", kind="pharmacy", clock=lambda: clock[0])
    assert len(overpass) == 2
    # Another kind is another query.
    map_places.places(near="-37.8136,144.9631", kind="fuel", clock=lambda: clock[0])
    assert len(overpass) == 3 and '["amenity"="fuel"]' in overpass[-1]


def test_a_cut_answer_asks_a_smaller_circle(monkeypatch):
    many = {"elements": [{"type": "node", "id": i, "lat": -37.8136, "lon": 144.9631,
                          "tags": {"amenity": "parking"}} for i in range(map_places.OUT_MAX)]}
    asked = []
    ok, got = map_places.places(near="-37.8136,144.9631", kind="parking", radius_m="20000",
                                fetch=lambda q: (asked.append(q), many)[1])
    # 20120 m, 5030, 1257, then 500: still cut, so it says so.
    assert ok and got["narrowed"] is True and len(asked) == 4 and got["truncated"] is True
    assert got["radius_m"] == 380 and "(around:500," in asked[-1]


@pytest.mark.parametrize("args, code", [
    ({"near": "-37.8,144.9"}, "no_kind"),
    ({"near": "-37.8,144.9", "kind": "spaceport"}, "unknown_kind"),
    ({"near": "-37.8,144.9", "kind": "raw[\"amenity\"]"}, "unknown_kind"),
    ({"kind": "cafe"}, "no_area"),
    ({"near": "north", "kind": "cafe"}, "bad_near"),
    ({"near": "-137.8,144.9", "kind": "cafe"}, "bad_near"),
    ({"near": "-37.8,144.9", "kind": "cafe", "radius_m": "far"}, "bad_radius"),
    ({"near": "-37.8,144.9", "kind": "cafe", "radius_m": "-5"}, "bad_radius"),
    ({"near": "-37.8,144.9", "bbox": "144,-38,145,-37", "kind": "cafe"}, "near_or_bbox"),
    ({"bbox": "144,-38", "kind": "cafe"}, "bad_bbox"),
    ({"bbox": "145,-38,144,-37", "kind": "cafe"}, "bad_bbox"),
    ({"bbox": "144,-38,145,-37", "kind": "cafe"}, "too_big"),
    ({"near": "-37.8,144.9", "kind": "cafe", "limit": "lots"}, "bad_limit"),
])
def test_validation(overpass, args, code):
    ok, got = map_places.places(**args)
    assert not ok and got["status"] == 400 and got["code"] == code
    assert overpass == []


def test_the_radius_is_capped(overpass):
    ok, got = map_places.places(near="-37.8,144.9", kind="cafe", radius_m="900000")
    assert ok and got["radius_m"] == map_places.MAX_RADIUS_M


def test_every_overpass_failing_is_502(monkeypatch):
    def down(query, timeout=None):
        raise OSError("connection refused")

    monkeypatch.setattr(map_osm, "overpass", down)
    ok, got = map_places.places(near="-37.8,144.9", kind="cafe")
    assert not ok and got["status"] == 502


def test_overpass_tries_each_address(monkeypatch):
    """map_osm.overpass, the client both the cameras and places use (the
    real one, kept before conftest's patch; urlopen faked)."""
    import io
    import json as _json
    import urllib.request

    monkeypatch.setenv("MEDIA_OVERPASS_URL", "http://a.invalid/api, http://b.invalid/api")
    tried = []

    def fake_open(req, timeout=None):
        tried.append(req.full_url)
        if "a.invalid" in req.full_url:
            raise OSError("busy")
        return io.BytesIO(_json.dumps({"elements": [1]}).encode())

    monkeypatch.setattr(urllib.request, "urlopen", fake_open)
    assert _REAL_OVERPASS("[out:json];") == {"elements": [1]}
    assert tried == ["http://a.invalid/api", "http://b.invalid/api"]
    tried.clear()

    def all_down(req, timeout=None):
        tried.append(req.full_url)
        raise OSError("away")

    monkeypatch.setattr(urllib.request, "urlopen", all_down)
    with pytest.raises(OSError):
        _REAL_OVERPASS("[out:json];")
    assert len(tried) == 2


def test_the_route_and_its_gate(server, monkeypatch, overpass):
    pixel, _ = _device("Pixel 8a")
    res, got = call(server, "GET", "/map/places?near=-37.8136,144.9631&kind=pharmacy,cafe", headers=pixel)
    assert res.status == 200 and got["kinds"] == ["pharmacy", "cafe"]
    assert {p["kind"] for p in got["places"]} == {"pharmacy"}
    res, got = call(server, "GET", "/map/places?near=-37.8136,144.9631&kind=spaceport", headers=pixel)
    assert res.status == 400 and got["code"] == "unknown_kind"
    res, got = call(server, "GET", "/map/places/kinds", headers=pixel)
    assert res.status == 200 and any(k["id"] == "ev_charger" for k in got["kinds"])
    # No bearer; the host's token; a guest.
    assert call(server, "GET", "/map/places?near=-37.8,144.9&kind=cafe")[0].status == 401
    monkeypatch.setenv("AMUX_AUTH_TOKEN", "hosttok")
    assert call(server, "GET", "/map/places?near=-37.8,144.9&kind=cafe",
                headers={"X-Auth-Token": "hosttok"})[0].status == 200
    hdrs, dev_id = _device("Visitor tablet")
    assert devices.set_role(dev_id, "guest")
    res, got = call(server, "GET", "/map/places?near=-37.8,144.9&kind=cafe", headers=hdrs)
    assert res.status == 403 and got.get("code") == "guest"
