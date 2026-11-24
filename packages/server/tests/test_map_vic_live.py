"""Victoria live (map_vic_live.py): VicEmergency's incidents and warnings and
the roads' closures as layers and in routing. No outside request is made:
conftest refuses map_vic_live._get, and each test passes its own fetch."""

from __future__ import annotations

import datetime as dt
import json
import urllib.error

import pytest

from agent_media_server import map_layers, map_route, map_vic_live as v

# A square Emergency Warning area round (145.0, -37.0), and a Watch and Act point.
EW_RING = [[144.99, -37.01], [145.01, -37.01], [145.01, -36.99], [144.99, -36.99], [144.99, -37.01]]
FEED = {"type": "FeatureCollection", "features": [
    {"type": "Feature", "geometry": {"type": "GeometryCollection", "geometries": [
        {"type": "Point", "coordinates": [145.0, -37.0]}, {"type": "Polygon", "coordinates": [EW_RING]}]},
     "properties": {"feedType": "warning", "category1": "Emergency Warning", "name": "Emergency Warning",
                    "sourceId": "501", "id": "501", "location": "Somewhere North", "action": "Leave now",
                    "cap": {"event": "Bushfire"}, "updated": "2026-11-24T12:00:00+11:00",
                    "webBody": "<div>It is <b>too late</b> to leave .</div><p>Shelter indoors.</p>"}},
    {"type": "Feature", "geometry": {"type": "Point", "coordinates": [145.2, -37.0]},
     "properties": {"feedType": "warning", "category1": "Watch and Act", "sourceId": "502", "id": "502",
                    "location": "East Town", "action": "Prepare to leave", "text": "WATCH AND ACT"}},
    {"type": "Feature", "geometry": {"type": "Point", "coordinates": [146.0, -38.0]},
     "properties": {"feedType": "incident", "category1": "Fire", "category2": "Bushfire", "id": "9",
                    "sourceTitle": "Nandaly - Mcfarlanes Road", "status": "Under Control", "sizeFmt": "954 Ha.",
                    "sourceOrg": "VIC/DEECA", "location": "3.9Km Ne Of Nandaly",
                    "updated": "2026-11-22T08:29:19Z"}},
    {"type": "Feature", "geometry": None, "properties": {"feedType": "incident", "id": "10"}},
]}

CLOSED_LINE = [[145.100, -37.500], [145.104, -37.500], [145.108, -37.500]]
UNPLANNED = {"meta": {"total_pages": 1}, "data": {"type": "FeatureCollection", "features": [
    {"type": "Feature", "geometry": {"type": "LineString", "coordinates": CLOSED_LINE},
     "properties": {"id": "u1", "status": "Active", "eventLocationStatus": "Active", "closedRoadName": "SMITH ROAD",
                    "impact": {"impactType": "Road Closed", "direction": "Both directions"},
                    "eventType": "Incident", "description": "Fallen tree", "reference":
                    {"startIntersectionRoadName": "JONES ST", "startIntersectionLocality": "KEW"}}},
    {"type": "Feature", "geometry": {"type": "Point", "coordinates": [145.3, -37.5]},
     "properties": {"id": "u2", "status": "Active", "impact": {"impactType": "Lanes closed"},
                    "closedRoadName": "HIGH ST"}},
    {"type": "Feature", "geometry": {"type": "Point", "coordinates": [145.4, -37.5]},
     "properties": {"id": "u3", "status": "Active", "eventLocationStatus": "Reopened",
                    "impact": {"impactType": "Road Closed"}}},
    {"type": "Feature", "geometry": {"type": "Point", "coordinates": [145.5, -37.5]},
     "properties": {"id": "u4", "status": "Active", "source": {"sourceName": "TowAllocation"}}},
]}}
PLANNED = {"type": "FeatureCollection", "nextPageDetails": {"hasMoreRecords": False}, "features": [
    # Roadworks on a 150 km corridor line: drawn at its point.
    {"type": "Feature", "geometry": {"type": "GeometryCollection", "geometries": [
        {"type": "Point", "coordinates": [145.6, -37.6]},
        {"type": "LineString", "coordinates": [[144.97, -37.69], [146.91, -36.10]]}]},
     "properties": {"id": "Planned:1", "status": "Active", "closedRoadName": "HUME FREEWAY",
                    "eventType": "Roadworks", "impact": {"impactType": "Speed reduction", "delay": "0 to 5 min"},
                    "duration": {"start": "2026-11-01T00:00:00", "end": "2026-12-31T00:00:00"}}},
    # Over: not shown.
    {"type": "Feature", "geometry": {"type": "Point", "coordinates": [145.7, -37.6]},
     "properties": {"id": "Planned:2", "status": "Active", "eventType": "Roadworks",
                    "duration": {"start": "2026-01-01T00:00:00", "end": "2026-02-01T00:00:00"}}},
]}
NOW = dt.datetime(2026, 11, 24, 13, 0, tzinfo=v.TZ).timestamp()


def _fetch(asked: list | None = None):
    def fetch(url: str, headers: dict) -> bytes:
        if asked is not None:
            asked.append((url, headers))
        if "events-geojson" in url:
            return json.dumps(FEED).encode()
        if "unplanned" in url:
            return json.dumps(UNPLANNED).encode()
        if "planned" in url:
            return json.dumps(PLANNED).encode()
        raise AssertionError(url)
    return fetch


@pytest.fixture
def vic(monkeypatch):
    monkeypatch.setenv("MEDIA_MAP_VIC", "1")
    monkeypatch.setattr(v, "_get", _fetch())
    v._reset_for_tests()


def test_off_unless_victoria_and_roads_need_the_key(monkeypatch):
    assert v.layers() == []
    monkeypatch.setenv("MEDIA_MAP_VIC", "1")
    assert [x["id"] for x in v.layers()] == ["vic_emergency"]
    monkeypatch.setenv("MEDIA_VIC_ROADS_KEY", "k")
    assert [x["id"] for x in v.layers()] == ["vic_emergency", "vic_roads"]
    monkeypatch.setenv("MEDIA_VIC_EMERGENCY", "0")
    assert [x["id"] for x in v.layers()] == ["vic_roads"]


def test_emergency_features_levels_areas_and_cards():
    got = v.emergency_features(FEED)
    ew = [f for f in got if f["properties"].get("level") == "emergency_warning"]
    assert {f["geometry"]["type"] for f in ew} == {"Point", "Polygon"}
    p = ew[0]["properties"]
    assert p["title"] == "Emergency Warning - Bushfire - Somewhere North"
    assert p["badge"] == "Emergency Warning" and p["colour"] == "#d32f2f" and p["card"] is True
    assert p["link"] == "https://emergency.vic.gov.au/respond/#!/warning/501/moreinfo"
    assert p["detail"].startswith("Leave now\n\nIt is too late to leave.")
    assert p["at"] == round(dt.datetime(2026, 11, 24, 1, 0, tzinfo=dt.timezone.utc).timestamp())
    wa = next(f for f in got if f["properties"].get("level") == "watch_and_act")["properties"]
    assert wa["colour"] == "#f57c00" and wa["rank"] == 3
    inc = next(f for f in got if f["properties"]["kind"] == "incident")["properties"]
    assert inc["title"] == "Bushfire: Nandaly - Mcfarlanes Road" and inc["badge"] == "Under Control"
    assert "Size: 954 Ha." in inc["detail"] and inc["link"] == v.EMERGENCY_PAGE
    assert len(got) == 4  # the feature with no geometry is left out


def test_layer_by_box_kept_and_stale(monkeypatch, vic):
    asked: list = []
    fetch = _fetch(asked)
    feats, extra = v.read_emergency((144.9, -37.1, 145.1, -36.9), fetch, NOW)
    assert {f["properties"]["source_id"] for f in feats} == {"501"} and extra == {}
    # Areas drawn under points.
    assert feats[0]["geometry"]["type"] == "Polygon"
    # Kept: asked once in the keep time.
    v.read_emergency(None, fetch, NOW + 30)
    assert len(asked) == 1

    def down(url, headers):
        raise urllib.error.URLError("down")
    # Past the keep time and the source down: the last copy, said to be stale.
    feats, extra = v.read_emergency(None, down, NOW + 200)
    assert len(feats) == 4 and extra == {"stale": True}
    # Past STALE_S: nothing, and said so.
    assert v.read_emergency(None, down, NOW + 200 + v.STALE_S + 100) == ([], {"unreachable": True})


def test_layers_route_through_map_layers(monkeypatch, vic):
    ok, got = map_layers.layers("", host=True)
    assert ok and "vic_emergency" in [x["id"] for x in got["layers"]]
    ok, got = map_layers.layer("vic_emergency", "144.9,-37.1,145.1,-36.9", "", host=True)
    assert ok and len(got["features"]) == 2
    assert map_layers.layer("vic_roads", "", "", host=True)[1]["status"] == 404


def test_road_kinds_and_times():
    assert v.road_kind({"impact": {"impactType": "Road Closed"}}, False) == "closed"
    assert v.road_kind({"roadAccessType": "Closed to all traffic"}, False) == "closed"
    assert v.road_kind({"impact": {"impactType": "Lanes closed"}}, False) == "lanes"
    assert v.road_kind({"impact": {"impactType": "One lane closed"}}, False) == "lanes"
    assert v.road_kind({"eventType": "Roadworks", "impact": {"impactType": "Speed reduction"}}, False) == "roadworks"
    assert v.road_kind({}, True) == "roadworks"
    # Planned times are local and naive; a recurrence must cover now.
    tue_1pm = NOW  # Tuesday 24 Nov 2026, 1 pm in Melbourne
    span = {"start": "2026-11-01T00:00:00", "end": "2026-12-01T00:00:00"}
    assert v.in_force({"duration": span}, tue_1pm) is True
    assert v.in_force({"duration": {"start": "2026-12-01T00:00:00"}}, tue_1pm) is False
    nights = {**span, "recurrences": [{"startDay": "Tuesday", "startTime": "20:00", "duration": "PT8H",
                                       "daysDuration": 1, "allDay": False}]}
    assert v.in_force({"duration": nights}, tue_1pm) is False
    assert v.in_force({"duration": nights}, tue_1pm + 8 * 3600) is True
    # Monday night's run carries past midnight into Tuesday.
    mon = {**span, "recurrences": [{"startDay": "Monday", "startTime": "22:00", "duration": "PT8H", "allDay": False}]}
    assert v.in_force({"duration": mon}, tue_1pm - 8 * 3600) is True
    assert v.in_force({"duration": {**span, "recurrences": [{"startDay": "x"}]}}, tue_1pm) is None


def test_roads_layer(monkeypatch, vic):
    monkeypatch.setenv("MEDIA_VIC_ROADS_KEY", "k3y")
    asked: list = []
    feats, extra = v.read_roads(None, _fetch(asked), NOW)
    by = {f["properties"]["source_id"]: f for f in feats}
    assert set(by) == {"u1", "u2", "Planned:1"}
    u1 = by["u1"]["properties"]
    assert by["u1"]["geometry"]["type"] == "LineString" and u1["road"] == "closed" and u1["avoid"] is True
    assert u1["title"] == "Road closed: Smith Road" and u1["where"] == "Jones St, Kew"
    assert "Fallen tree" in u1["detail"] and u1["colour"] == "#9b111e"
    assert by["u2"]["properties"]["road"] == "lanes" and "avoid" not in by["u2"]["properties"]
    p1 = by["Planned:1"]
    assert p1["geometry"] == {"type": "Point", "coordinates": [145.6, -37.6]}
    assert p1["properties"]["road"] == "roadworks" and p1["properties"]["dashed"] is True
    # Closures over roadworks; the key in KeyID (and the APIM name) on both feeds.
    assert feats[-1]["properties"]["road"] == "closed"
    assert all(h["KeyID"] == "k3y" and h["Ocp-Apim-Subscription-Key"] == "k3y" for _u, h in asked)
    assert any("unplanned/v3?page=1&limit=1000" in u for u, _h in asked)
    assert any("planned/v1/?format=GeoJson" in u for u, _h in asked)


def test_exclusions_inside_the_closure_not_at_trip_ends(monkeypatch, vic):
    monkeypatch.setenv("MEDIA_VIC_ROADS_KEY", "k")
    locs, n = v.exclusions([(-37.55, 145.0), (-37.45, 145.2)], _fetch(), NOW)
    assert n == 1 and 1 <= len(locs) <= 4
    assert all(145.100 < x["lon"] < 145.108 and x["lat"] == -37.5 for x in locs)
    # Going to the closed road itself: not excluded, or there would be no route.
    assert v.exclusions([(-37.55, 145.0), (-37.5, 145.104)], _fetch(), NOW) == ([], 0)
    # Far from the trip: nothing.
    assert v.exclusions([(-36.0, 147.0), (-36.1, 147.1)], _fetch(), NOW) == ([], 0)


def test_hazards_on_a_line(monkeypatch, vic):
    monkeypatch.setenv("MEDIA_VIC_ROADS_KEY", "k")
    # Through the Emergency Warning square, past the Watch and Act point within a km.
    line = [[144.95, -37.0], [145.205, -37.005]]
    h = v.hazards_on(line, "drive", _fetch(), NOW)
    assert [a["level"] for a in h["alerts"]] == ["emergency_warning", "watch_and_act"]
    assert h["alerts"][0]["area"] == [EW_RING] and "area" not in h["alerts"][1]
    # Along the closed road: a closure alert.
    h = v.hazards_on([[145.09, -37.5], [145.12, -37.5]], "drive", _fetch(), NOW)
    assert [a["kind"] for a in h["alerts"]] == ["closure"] and h["alerts"][0]["title"] == "Road closed: Smith Road"
    # Walking: emergencies only. Nowhere near anything: None.
    assert v.hazards_on([[145.09, -37.5], [145.12, -37.5]], "walk", _fetch(), NOW) is None
    assert v.hazards_on([[140.0, -30.0], [140.1, -30.1]], "drive", _fetch(), NOW) is None
    # Avoided closures are said even when nothing is met.
    assert v.hazards_on([[140.0, -30.0], [140.1, -30.1]], "drive", _fetch(), NOW, avoided=2)["avoided"] == 2


def test_route_avoids_closures_and_carries_hazards(monkeypatch, vic):
    monkeypatch.setenv("MEDIA_VIC_ROADS_KEY", "k")
    monkeypatch.setattr(v, "_get", _fetch())
    asked: list = []
    trip = {"trip": {"summary": {"length": 30.0, "time": 1200}, "legs": [{"shape": "", "maneuvers": []}]}}

    def ask(base, path, body=None, timeout=0):
        asked.append(body)
        return trip
    monkeypatch.setattr(map_route, "_ask", ask)
    monkeypatch.setattr(map_route, "decode_polyline", lambda s, precision=6: [[144.95, -37.0], [145.05, -37.0]])
    ok, got = map_route.route("-37.55,145.0", "-37.45,145.2", "drive", base="http://valhalla.invalid", traffic="0")
    assert ok, got
    assert asked[0]["exclude_locations"] and all(145.1 < x["lon"] < 145.108 for x in asked[0]["exclude_locations"])
    assert got["hazards"]["avoided"] == 1 and got["hazards"]["alerts"][0]["level"] == "emergency_warning"
    assert got["routes"][0]["hazards"] == got["hazards"]

    # No route round the closure: asked again without it, nothing said to be avoided.
    asked.clear()

    def refuse_excluded(base, path, body=None, timeout=0):
        asked.append(body)
        if body and body.get("exclude_locations"):
            raise urllib.error.HTTPError("u", 400, "no", {}, None)
        return trip
    monkeypatch.setattr(map_route, "_ask", refuse_excluded)
    ok, got = map_route.route("-37.55,145.0", "-37.45,145.2", "drive", base="http://valhalla.invalid", traffic="0")
    assert ok and "exclude_locations" not in asked[1] and got["hazards"]["avoided"] == 0
    # Walking: no closures asked of Valhalla.
    asked.clear()
    monkeypatch.setattr(map_route, "_ask", ask)
    map_route.route("-37.55,145.0", "-37.45,145.2", "walk", base="http://valhalla.invalid")
    assert "exclude_locations" not in asked[0]
