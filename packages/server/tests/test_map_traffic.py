"""Live traffic (map_traffic.py, §6.50): the `traffic` layer from TomTom's flow
and incident tiles, routes timed in traffic, and the monthly ceilings."""

from __future__ import annotations

import datetime as dt
import json
import struct

from agent_media_server import map_route, map_traffic

from test_contract import call, server, shelf, signed_in, typed  # noqa: F401
from test_mic import _device


# --- a vector tile, written ---------------------------------------------------

def _v(n: int) -> bytes:
    out = bytearray()
    while n >= 0x80:
        out.append((n & 0x7F) | 0x80)
        n >>= 7
    out.append(n)
    return bytes(out)


def _field(num: int, payload: bytes) -> bytes:
    return _v(num << 3 | 2) + _v(len(payload)) + payload


def _zz(n: int) -> int:
    return (n << 1) ^ (n >> 31)


def _value(x) -> bytes:
    if isinstance(x, str):
        return _field(1, x.encode())
    if isinstance(x, bool):
        return _v(7 << 3) + _v(int(x))
    if isinstance(x, float):
        return _v(3 << 3 | 1) + struct.pack("<d", x)
    return _v(5 << 3) + _v(x)


def _tile(layers: dict[str, list[tuple[int, list[tuple[int, int]], dict]]]) -> bytes:
    """{layer: [(type, points, props)]} as a Mapbox Vector Tile."""
    out = b""
    for name, feats in layers.items():
        keys: list[str] = []
        vals: list = []
        body = _field(1, name.encode())
        for typ, pts, props in feats:
            tags = []
            for k, val in props.items():
                if k not in keys:
                    keys.append(k)
                if val not in vals:
                    vals.append(val)
                tags += [keys.index(k), vals.index(val)]
            geom, x, y = [], 0, 0
            for i, (px, py) in enumerate(pts):
                if i == 0:
                    geom.append(1 | (1 << 3))
                elif i == 1:
                    geom.append(2 | ((len(pts) - 1) << 3))
                geom += [_zz(px - x), _zz(py - y)]
                x, y = px, py
            feat = _field(2, b"".join(_v(t) for t in tags)) + _v(3 << 3) + _v(typ) \
                + _field(4, b"".join(_v(g) for g in geom))
            body += _field(2, feat)
        body += b"".join(_field(3, k.encode()) for k in keys) + b"".join(_field(4, _value(v)) for v in vals)
        body += _v(5 << 3) + _v(4096) + _v(15 << 3) + _v(2)
        out += _field(3, body)
    return out


FLOW = _tile({"Traffic flow": [
    (2, [(0, 0), (2048, 2048)], {"traffic_level": 0.2, "road_category": "primary"}),
    (2, [(100, 4000), (4000, 100)], {"traffic_level": 0.95}),
    (2, [(10, 10), (20, 20)], {"traffic_level": 0.6, "road_closure": True})]})
INCIDENTS = _tile({
    "Traffic incident POI": [
        (1, [(1000, 1000)], {"icon_category_0": 1, "description_0": "Crash", "delay": 420, "magnitude": 3,
                             "poi_type": "start_poi"}),
        (1, [(1500, 1500)], {"icon_category_0": 1, "poi_type": "end_poi"}),
        (1, [(3000, 3000)], {"icon_category_0": 6, "description_0": "Stationary traffic", "poi_type": "start_poi"})],
    "Traffic incident flow": [
        (2, [(0, 4000), (4000, 4000)], {"icon_category_0": 8, "description_0": "Closed"})]})
CLUSTER = _tile({"Traffic incident POI": [
    (1, [(2000, 2000)], {"icon_category": 13, "description": "Cluster", "cluster_size": 3, "poi_type": "standalone_poi"})]})


def test_a_cluster_says_how_many():
    (f,) = map_traffic._incidents(12, 3699, 2514, CLUSTER)
    p = f["properties"]
    assert p["incident"] == "several" and p["count"] == 3 and p["detail"].startswith("3 together")


def test_a_tile_is_read():
    lays = map_traffic.decode_mvt(FLOW)
    (f1, f2, f3) = lays["Traffic flow"]["features"]
    assert f1["type"] == 2 and f1["parts"] == [[(0, 0), (2048, 2048)]]
    assert f1["props"] == {"traffic_level": 0.2, "road_category": "primary"} and f3["props"]["road_closure"] is True
    # Tile coordinates as lon, lat: zoom 1's top-left tile ends at 0, 0.
    assert map_traffic._lonlat(1, 0, 0, 4096, 4096, 4096) == [0.0, 0.0]


def test_levels_like_googles():
    assert map_traffic.level_of(0.9, False) == "free" and map_traffic.level_of(0.6, False) == "slow"
    assert map_traffic.level_of(0.2, False) == "jam" and map_traffic.level_of(0.9, True) == "closed"


def test_no_key_no_layer(server):
    pixel, _ = _device("Pixel 8a")
    lays = call(server, "GET", "/map/layers", headers=pixel)[1]["layers"]
    assert not [x for x in lays if x["id"] == "traffic"]
    res, got = call(server, "GET", "/map/traffic?bbox=144.9,-37.9,145.0,-37.8", headers=pixel)
    assert res.status == 404 and got["code"] == "no_traffic"


def test_the_traffic_layer_kept_and_counted(server, monkeypatch, tmp_path):
    monkeypatch.setenv("MEDIA_TOMTOM_API_KEY", "k3y")
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    asked = []

    def fetch(url, headers):
        asked.append(url)
        return 200, (FLOW if "/flow/relative/" in url else INCIDENTS)
    monkeypatch.setattr(map_traffic, "_get", fetch)
    pixel, _ = _device("Pixel 8a")
    (lay,) = [x for x in call(server, "GET", "/map/layers", headers=pixel)[1]["layers"] if x["id"] == "traffic"]
    assert lay["kind"] == "flow" and lay["bbox"] is True and lay["default_on"] is False
    assert "TomTom" in lay["source"] and "k3y" not in json.dumps(lay)
    box = "144.95,-37.82,144.97,-37.80"
    res, got = call(server, "GET", f"/map/layer?id=traffic&bbox={box}", headers=pixel)
    assert res.status == 200, got
    tiles = len(asked)
    assert tiles % 2 == 0 and 2 <= tiles <= 2 * map_traffic.MAX_TILES
    assert all("key=k3y" in u for u in asked) and any("tags=" in u for u in asked)
    lines = [f for f in got["features"] if f["geometry"]["type"] == "LineString"]
    pins = [f for f in got["features"] if f["geometry"]["type"] == "Point"]
    levels = {f["properties"]["level"] for f in lines}
    assert levels == {"jam", "free", "closed"}
    jam = next(f for f in lines if f["properties"]["level"] == "jam")
    assert jam["properties"]["ratio"] == 0.2 and jam["properties"]["colour"] == "#d93025"
    # Only crashes (an incident's far end and a queue are not pins), each with its words and delay;
    # the layer keeps those inside the box.
    assert pins and {p["properties"]["incident"] for p in pins} == {"crash"} and len(pins) <= tiles // 2
    assert pins[0]["properties"]["title"] == "Crash" and "7 min delay" in pins[0]["properties"]["detail"]
    # A closure's stretch drawn as a closed road.
    assert any(f["properties"].get("closure") for f in lines)
    # Kept: the same box again asks nothing.
    call(server, "GET", f"/map/layer?id=traffic&bbox={box}", headers=pixel)
    assert len(asked) == tiles and map_traffic.usage("traffic") == tiles
    # /map/traffic gives flow and incidents apart.
    res, got = call(server, "GET", f"/map/traffic?bbox={box}", headers=pixel)
    assert res.status == 200 and got["incidents"] and got["flow"] and got["credit"] == "Traffic © TomTom"
    # Too wide a box: zoom in, nothing asked.
    got = call(server, "GET", "/map/layer?id=traffic&bbox=140,-39,150,-34", headers=pixel)[1]
    assert got["zoom_in"] is True and got["features"] == [] and len(asked) == tiles


def test_the_ceiling_stops_the_tiles(monkeypatch, tmp_path):
    monkeypatch.setenv("MEDIA_TOMTOM_API_KEY", "k3y")
    monkeypatch.setenv("MEDIA_TOMTOM_TRAFFIC_MONTHLY_MAX", "1")
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    asked = []
    monkeypatch.setattr(map_traffic, "_get", lambda u, h: (asked.append(u), (200, FLOW))[1])
    feats, extra = map_traffic.read((144.95, -37.82, 144.97, -37.80))
    assert len(asked) == 1 and extra["month_spent"] is True and extra["partial"] is True
    # A new month starts again.
    assert map_traffic._spend("traffic", now=dt.datetime(2099, 1, 1, tzinfo=dt.timezone.utc))
    # 0 turns the layer off.
    monkeypatch.setenv("MEDIA_TOMTOM_TRAFFIC_MONTHLY_MAX", "0")
    assert map_traffic.layers() == [] and map_traffic.read((144.95, -37.82, 144.97, -37.80))[1] == {"off": True}


def test_routes_timed_in_traffic_and_fastest_first(monkeypatch, tmp_path):
    monkeypatch.setenv("MEDIA_TOMTOM_API_KEY", "k3y")
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    posted = []

    def post(url, body):
        posted.append((url, body))
        n = len(body["supportingPoints"])
        # The second way is quicker in today's traffic.
        t = 1500 if n == 3 else 1200
        return {"routes": [{"summary": {"travelTimeInSeconds": t, "trafficDelayInSeconds": 360,
                                        "noTrafficTravelTimeInSeconds": t - 360}}]}
    monkeypatch.setattr(map_traffic, "_post", post)
    a = {"mode": "drive", "duration_s": 900, "line": [[144.96, -37.81], [144.97, -37.82], [144.98, -37.83]]}
    b = {"mode": "drive", "duration_s": 950, "line": [[144.96, -37.81], [144.965, -37.825], [144.975, -37.83],
                                                      [144.98, -37.83]]}
    got = map_traffic.annotate([a, b])
    assert got[0] is b and b["traffic"] == {"duration_s": 1200, "delay_s": 360, "no_traffic_s": 840,
                                            "credit": "Traffic © TomTom"}
    url, body = posted[0]
    assert "calculateRoute/-37.810000,144.960000:-37.830000,144.980000/json" in url and "traffic=true" in url
    assert body["supportingPoints"][1] == {"latitude": -37.82, "longitude": 144.97}
    # The same line within two minutes: kept.
    map_traffic.annotate([a])
    assert len(posted) == 2
    # Walking is never timed; at the ceiling, routes keep Valhalla's times.
    assert "traffic" not in map_traffic.annotate([{"mode": "walk", "line": a["line"]}])[0]
    monkeypatch.setenv("MEDIA_TOMTOM_ROUTING_MONTHLY_MAX", "2")
    assert map_traffic.eta([[1, 2], [3, 4]]) is None and len(posted) == 2
    # A long line is thinned, its ends kept.
    thin = map_traffic._thin([[i, i] for i in range(1000)])
    assert len(thin) == map_traffic.MAX_POINTS and thin[0] == [0, 0] and thin[-1] == [999, 999]


def test_route_and_eta_routes(server, monkeypatch, tmp_path):
    """GET /map/route carries `traffic` for a drive now, not for leave-at; POST /map/traffic/eta times a line."""
    monkeypatch.setenv("MEDIA_TOMTOM_API_KEY", "k3y")
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("MEDIA_ROUTER_URL", "http://valhalla.invalid")
    trip = {"trip": {"summary": {"length": 1.0, "time": 120}, "legs": [{"shape": "", "maneuvers": []}]}}
    monkeypatch.setattr(map_route, "_ask", lambda base, path, body=None, timeout=0: trip)
    monkeypatch.setattr(map_route, "decode_polyline", lambda s, precision=6: [[144.96, -37.81], [144.98, -37.83]])
    monkeypatch.setattr(map_traffic, "_post", lambda url, body: {"routes": [{"summary": {
        "travelTimeInSeconds": 480, "trafficDelayInSeconds": 300}}]})
    pixel, _ = _device("Pixel 8a")
    res, got = call(server, "GET", "/map/route?from=-37.81,144.96&to=-37.83,144.98&mode=drive", headers=pixel)
    assert res.status == 200, got
    assert got["traffic"]["duration_s"] == 480 and got["routes"][0]["traffic"]["delay_s"] == 300
    got = call(server, "GET", "/map/route?from=-37.81,144.96&to=-37.83,144.98&mode=drive&depart=2099-01-01T08:00",
               headers=pixel)[1]
    assert "traffic" not in got
    got = call(server, "GET", "/map/route?from=-37.81,144.96&to=-37.83,144.98&mode=drive&traffic=0", headers=pixel)[1]
    assert "traffic" not in got
    res, got = call(server, "POST", "/map/traffic/eta", {"line": [[144.96, -37.81], [144.99, -37.84]]}, pixel)
    assert res.status == 200 and got["traffic"]["duration_s"] == 480
    assert call(server, "POST", "/map/traffic/eta", {"line": [[1, 2]]}, pixel)[0].status == 400
    # Behind the map's gate.
    assert call(server, "POST", "/map/traffic/eta", {"line": [[1, 2], [3, 4]]})[0].status == 401
