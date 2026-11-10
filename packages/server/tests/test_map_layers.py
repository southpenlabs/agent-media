"""`GET /map/layers` and `GET /map/layer`: the Map page's layers
(map_layers.py, §6.50; docs/proposals/2026-11-06-maps.md)."""

from __future__ import annotations

import json
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
    assert ids == ["phone", "speed_cameras", "surveillance", "cams", "broken"]
    got = call(server, "GET", "/map/layer?id=cams", headers=pixel)[1]
    assert got["features"][0]["properties"]["title"] == "Cam"
    assert call(server, "GET", "/map/layer?id=broken", headers=pixel)[0].status == 502


SID = "0123abcd-0000-4000-8000-000000000001"


def _msgs():
    return [{"id": "m1", "role": "user", "parts": [{"type": "text", "text": "Where for coffee?"}]},
            {"id": "m2", "role": "assistant", "parts": [{"type": "text", "text":
                "Two near you.\n[[map: Brunetti @ -37.7981,144.9687 | Seven Seeds @ -37.8003,144.9581]]"}]},
            {"id": "m3", "role": "assistant", "parts": [{"type": "text", "text":
                "The walk: [[route: Home @ -37.80,144.96 | Park @ -37.79,144.97]] about 20 minutes."}]}]


def test_a_replys_places_are_on_the_message_and_out_of_its_words():
    from agent_media_server import transcript

    msgs = _msgs()
    transcript.strip_markers(msgs)
    assert msgs[1]["parts"][0]["text"] == "Two near you."
    assert msgs[1]["places"] == [{"kind": "pins", "points": [
        {"name": "Brunetti", "lat": -37.7981, "lon": 144.9687},
        {"name": "Seven Seeds", "lat": -37.8003, "lon": 144.9581}]}]
    assert msgs[2]["parts"][0]["text"] == "The walk: about 20 minutes."
    assert msgs[2]["places"][0]["kind"] == "route"
    assert "places" not in msgs[0]


def test_a_chats_places_are_a_layer(server, monkeypatch):
    from agent_media_server import transcript

    pixel, _ = _device("Pixel 8a")
    monkeypatch.setattr(transcript, "messages", lambda s, **k: (_msgs(), False) if s == SID else None)
    lays = call(server, "GET", f"/map/layers?thread={SID}", headers=pixel)[1]["layers"]
    assert lays[0] == {"id": f"thread:{SID}", "name": "Places in this chat", "kind": "points",
                       "icon": "pin", "default_on": True}
    got = call(server, "GET", f"/map/layer?id=thread:{SID}", headers=pixel)[1]["features"]
    points = [f["properties"]["title"] for f in got if f["geometry"]["type"] == "Point"]
    assert points == ["Brunetti", "Seven Seeds", "Home", "Park"]
    (line,) = [f for f in got if f["geometry"]["type"] == "LineString"]
    assert line["properties"]["title"] == "Home → Park" and line["properties"]["msg"] == "m3"
    assert call(server, "GET", "/map/layers?thread=nope", headers=pixel)[0].status == 400


OVERPASS = [
    {"type": "node", "id": 1, "lat": -37.81, "lon": 144.96, "tags": {"highway": "speed_camera", "maxspeed": "60"}},
    {"type": "relation", "id": 9, "tags": {"type": "enforcement", "enforcement": "traffic_signals"},
     "members": [{"type": "node", "ref": 2, "role": "device"}, {"type": "node", "ref": 3, "role": "from"}]},
    {"type": "node", "id": 2, "lat": -37.815, "lon": 144.965, "tags": {}},
    {"type": "node", "id": 3, "lat": -37.816, "lon": 144.966, "tags": {}},
]


def test_speed_cameras_from_osm_for_the_box_on_screen(server, monkeypatch):
    from agent_media_server import map_osm

    pixel, _ = _device("Pixel 8a")
    asked = []
    monkeypatch.setattr(map_osm, "_fetch", lambda layer, sq: (asked.append(sq), OVERPASS)[1])
    # The worker, run in place: the square is fetched as it is asked for.
    monkeypatch.setattr(map_osm, "_ask", map_osm._fill)
    lays = {lay["id"]: lay for lay in call(server, "GET", "/map/layers", headers=pixel)[1]["layers"]}
    assert lays["speed_cameras"]["bbox"] is True and lays["speed_cameras"]["default_on"] is False
    # No box, or one too wide: zoom in.
    got = call(server, "GET", "/map/layer?id=speed_cameras", headers=pixel)[1]
    assert got["features"] == [] and got["zoom_in"] is True
    got = call(server, "GET", "/map/layer?id=speed_cameras&bbox=140,-40,150,-30", headers=pixel)[1]
    assert got["zoom_in"] is True and asked == []
    # A box in one square: the first read says partial (fetched behind it),
    # the next has it, and later ones come from the cache.
    box = "144.95,-37.82,144.97,-37.805"
    got = call(server, "GET", f"/map/layer?id=speed_cameras&bbox={box}", headers=pixel)[1]
    assert got["features"] == [] and got["partial"] is True and len(asked) == 1
    got = call(server, "GET", f"/map/layer?id=speed_cameras&bbox={box}", headers=pixel)[1]
    titles = sorted(f["properties"]["title"] for f in got["features"])
    assert titles == ["Red light camera", "Speed camera 60"] and got["partial"] is False and got["as_of"] > 0
    n = len(asked)
    call(server, "GET", f"/map/layer?id=speed_cameras&bbox={box}", headers=pixel)
    assert len(asked) == n


def test_the_squares_go_to_the_worker_and_off_by_setting(server, monkeypatch):
    from agent_media_server import map_osm

    pixel, _ = _device("Pixel 8a")
    queued = []
    monkeypatch.setattr(map_osm, "_ask", lambda layer, sq: queued.append(sq))
    got = call(server, "GET", "/map/layer?id=surveillance&bbox=144.0,-38.0,144.45,-37.7", headers=pixel)[1]
    assert got["partial"] is True and len(queued) == 20  # 4 rows by 5 columns, all to the worker
    monkeypatch.setenv("MEDIA_MAP_SPEED_CAMERAS", "off")
    ids = [lay["id"] for lay in call(server, "GET", "/map/layers", headers=pixel)[1]["layers"]]
    assert "speed_cameras" not in ids and "surveillance" in ids


OSM_XML = """<?xml version='1.0' encoding='UTF-8'?>
<osm version="0.6">
 <node id="1" lat="-37.81" lon="144.96" version="1"><tag k="highway" v="speed_camera"/><tag k="maxspeed" v="60"/></node>
 <node id="2" lat="-37.815" lon="144.965" version="1"/>
 <node id="3" lat="-37.80" lon="144.95" version="1"><tag k="man_made" v="surveillance"/><tag k="operator" v="City"/></node>
 <node id="4" lat="-37.70" lon="144.90" version="1"><tag k="amenity" v="cafe"/></node>
 <relation id="9" version="1"><member type="node" ref="2" role="device"/>
  <tag k="type" v="enforcement"/><tag k="enforcement" v="traffic_signals"/></relation>
</osm>
"""


def test_the_weekly_file_keeps_only_cameras(server, monkeypatch, tmp_path):
    import pytest

    pytest.importorskip("osmium")
    from agent_media_server import map_osm

    pbf = tmp_path / "x.osm"
    pbf.write_text(OSM_XML)
    elements = map_osm._elements(str(pbf))
    assert sorted(e["id"] for e in elements if e["type"] == "node") == [1, 2, 3]
    # refresh, with the download faked: the file is "fetched" from disk.
    monkeypatch.setattr(map_osm.urllib.request, "urlopen",
                        lambda req, timeout=0: open(pbf, "rb"))
    monkeypatch.setattr(map_osm, "_cache_dir", lambda: tmp_path)
    # open() has no headers; a bare file reads fine as the response.
    class _Res:
        def __init__(self):
            self.f = open(pbf, "rb")
            self.headers = {"Last-Modified": "Sun, 08 Nov 2026 23:44:53 GMT"}

        def read(self, n):
            return self.f.read(n)

        def __enter__(self):
            return self

        def __exit__(self, *a):
            self.f.close()

    monkeypatch.setattr(map_osm.urllib.request, "urlopen", lambda req, timeout=0: _Res())
    monkeypatch.setattr(map_osm, "_elements", lambda p: elements)
    assert map_osm.refresh("https://example.invalid/x.osm.pbf") == {"speed_cameras": 2, "surveillance": 1, "sequence": None}
    assert not (tmp_path / "extract.osm.pbf").exists()
    monkeypatch.setenv("MEDIA_MAP_OSM_EXTRACT", "https://example.invalid/x.osm.pbf")
    pixel, _ = _device("Pixel 8a")
    got = call(server, "GET", "/map/layer?id=speed_cameras&bbox=144.95,-37.82,144.97,-37.805", headers=pixel)[1]
    assert sorted(f["properties"]["title"] for f in got["features"]) == ["Red light camera", "Speed camera 60"]
    assert got["partial"] is False and got["as_of"] == 1794181493


def test_a_change_set_updates_the_kept_cameras():
    from agent_media_server import map_osm

    kept = [{"type": "relation", "id": 9, "tags": {"type": "enforcement", "enforcement": "traffic_signals"},
             "members": [{"type": "node", "ref": 2, "role": "device"}]},
            {"type": "node", "id": 1, "lat": -37.81, "lon": 144.96, "tags": {"highway": "speed_camera"}},
            {"type": "node", "id": 2, "lat": -37.815, "lon": 144.965, "tags": {}},
            {"type": "node", "id": 3, "lat": -37.8, "lon": 144.95, "tags": {"man_made": "surveillance"}}]
    changes = [
        ("node", 1, True, {}, None, None, None),                                   # removed
        ("node", 3, False, {"amenity": "bench"}, -37.8, 144.95, None),             # no longer a camera
        ("node", 5, False, {"highway": "speed_camera", "maxspeed": "80"}, -37.9, 145.0, None),  # new
        ("relation", 9, False, {"type": "enforcement", "enforcement": "average_speed"}, None, None, [(2, "device")]),
    ]
    got = map_osm.apply_changes(kept, changes)
    titles = sorted(f["properties"]["title"] for f in map_osm._features("speed_cameras", got))
    assert titles == ["Average speed camera", "Speed camera 80"]
    assert map_osm._features("surveillance", got) == []
    # The relation gone: its device node goes with it.
    got = map_osm.apply_changes(got, [("relation", 9, True, {}, None, None, None)])
    assert sorted(e["id"] for e in got) == [5]


def test_the_change_files_sit_beside_the_extract(monkeypatch):
    from agent_media_server import map_osm

    assert map_osm._updates_url("https://download.geofabrik.de/australia-oceania/australia-latest.osm.pbf") == \
        "https://download.geofabrik.de/australia-oceania/australia-updates"
    monkeypatch.setenv("MEDIA_MAP_OSM_UPDATES", "https://x.example/u/")
    assert map_osm._updates_url("anything") == "https://x.example/u"


def _xlsx(rows):
    """A one-sheet .xlsx with inline strings, as Victoria's list is laid out."""
    import io
    import zipfile

    def cell(v):
        return (f'<c t="inlineStr"><is><t>{v}</t></is></c>' if isinstance(v, str) else f"<c><v>{v}</v></c>")
    xml = ('<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>'
           + "".join("<row>" + "".join(cell(v) for v in r) + "</row>" for r in rows)
           + "</sheetData></worksheet>")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("xl/worksheets/sheet1.xml", xml)
    return buf.getvalue()


VIC = _xlsx([["MOBILE PHONE AND SEATBELT CAMERA LOCATIONS"], ["Road", "Suburb", "Reason Code ", "Effective Date "],
             ["Queen Street", "ALTONA", "A", 46331], ["Bass Highway", "ANDERSON", "A", 46331],
             ["Bass Highway", "ANDERSON", "B", 46331], ["Nowhere Lane", "NOWHERE", "A", 46331],
             ["C - Identified by Police as deemed necessary. "]])


def test_victorias_list_read_drawn_as_roads_and_off_by_default(server, monkeypatch):
    from agent_media_server import map_vic

    sites, eff = map_vic.read_sites(VIC)
    assert [(s["road"], s["suburb"], s["count"]) for s in sites] == [
        ("Queen Street", "Altona", 1), ("Bass Highway", "Anderson", 2), ("Nowhere Lane", "Nowhere", 1)]
    assert eff == 1793836800.0  # 1 Nov 2026
    page = ('<a href="/sites/default/files/2026-09/DDS_camera_locations_October-2026.xlsx">'
            '<a href="https://www.vic.gov.au/sites/default/files/2026-10/DDS_camera_locations_November-2026.xlsx">')
    assert map_vic.newest_file(page).endswith("2026-10/DDS_camera_locations_November-2026.xlsx")

    lines = {("Queen Street", "Altona"): {"type": "LineString", "coordinates": [[144.79, -37.874], [144.80, -37.876]]},
             ("Bass Highway", "Anderson"): {"type": "LineString", "coordinates": [[145.44, -38.52], [145.45, -38.53]]}}
    monkeypatch.setattr(map_vic, "_get", lambda url, timeout=60: page.encode() if url == map_vic.PAGE else VIC)
    monkeypatch.setattr(map_vic, "_road_line", lambda road, suburb: lines.get((road, suburb)))
    monkeypatch.setattr(map_vic.time, "sleep", lambda s: None)
    assert map_vic.refresh() == {"file": map_vic.SITE + "/sites/default/files/2026-10/DDS_camera_locations_November-2026.xlsx",
                                 "sites": 3, "drawn": 2, "missed": 1}
    assert map_vic.refresh()["unchanged"] is True

    pixel, _ = _device("Pixel 8a")
    ids = [lay["id"] for lay in call(server, "GET", "/map/layers", headers=pixel)[1]["layers"]]
    assert "vic_phone_seatbelt" not in ids
    monkeypatch.setenv("MEDIA_MAP_VIC", "1")
    ids = [lay["id"] for lay in call(server, "GET", "/map/layers", headers=pixel)[1]["layers"]]
    assert "vic_phone_seatbelt" in ids
    got = call(server, "GET", "/map/layer?id=vic_phone_seatbelt&bbox=145.3,-38.6,145.5,-38.4", headers=pixel)[1]
    assert [f["properties"]["detail"] for f in got["features"]] == ["Bass Highway, Anderson (2 sites)"]
    assert got["as_of"] == 1793836800


def test_the_host_token_reads_the_map(server, monkeypatch):
    monkeypatch.setenv("AMUX_AUTH_TOKEN", "hosttok")
    res, obj = call(server, "GET", "/map/layers", headers={"X-Auth-Token": "hosttok"})
    assert res.status == 200 and obj["layers"][0]["id"] == "phone"
    assert call(server, "GET", "/map/layers", headers={"X-Auth-Token": "wrong"})[0].status == 401


def test_the_phone_is_handed_the_cameras_around_it(server, monkeypatch, tmp_path):
    from agent_media_server import map_osm, map_vic

    pixel, _ = _device("Pixel 8a")
    elements = [
        {"type": "node", "id": 1, "lat": -37.81, "lon": 144.96, "tags": {"highway": "speed_camera", "maxspeed": "60"}},
        {"type": "relation", "id": 9, "tags": {"type": "enforcement", "enforcement": "traffic_signals"},
         "members": [{"type": "node", "ref": 2, "role": "device"}]},
        {"type": "node", "id": 2, "lat": -37.815, "lon": 144.965, "tags": {}},
        {"type": "relation", "id": 8, "tags": {"type": "enforcement", "enforcement": "mobile_phone"},
         "members": [{"type": "node", "ref": 3, "role": "device"}]},
        {"type": "node", "id": 3, "lat": -37.816, "lon": 144.966, "tags": {}},
        {"type": "node", "id": 4, "lat": -36.0, "lon": 146.0, "tags": {"highway": "speed_camera"}},  # far away
    ]
    monkeypatch.setenv("MEDIA_MAP_OSM_EXTRACT", "https://example.invalid/x.osm.pbf")
    monkeypatch.setattr(map_osm, "_extract", {"mtime": None, "data": None})
    map_osm._keep(elements, 1794181493, "https://example.invalid/x.osm.pbf", None)
    got = call(server, "GET", "/map/alerts?near=-37.81,144.96", headers=pixel)[1]
    assert sorted((p["kind"], p.get("limit")) for p in got["points"]) == [("phone", None), ("red_light", None), ("speed", "60")]
    assert got["roads"] == [] and got["radius_km"] == 25.0
    # Victoria's roads, where on.
    monkeypatch.setenv("MEDIA_MAP_VIC", "1")
    (tmp_path / "state" / "agent-media" / "vic-cameras.json").write_text(json.dumps({"as_of": 1, "features": [
        {"type": "Feature", "properties": {"title": "Phone & seatbelt camera site", "detail": "Queen Street, Altona (2 sites)"},
         "geometry": {"type": "LineString", "coordinates": [[144.79, -37.874], [144.80, -37.876]]}}]}))
    got = call(server, "GET", "/map/alerts?near=-37.85,144.85", headers=pixel)[1]
    assert got["roads"] == [{"name": "Queen Street", "where": "Queen Street, Altona", "kind": "phone_seatbelt",
                             "lines": [[[144.79, -37.874], [144.80, -37.876]]]}]
    assert call(server, "GET", "/map/alerts?near=nowhere", headers=pixel)[0].status == 400
