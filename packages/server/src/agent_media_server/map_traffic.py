"""Live traffic on the Map, as Google Maps shows it (maps proposal step 7
item 4; David, 19 Nov 2026: "let's actually add a whole lot of features
based on Google maps").

Where it comes from, looked at in this order (22 Nov 2026):

- Open data first. Victoria's Department of Transport and Planning publishes
  "Freeway Travel Time" (Transport Victoria Open Data Portal, CC BY 4.0):
  travel times from the freeways' roadside sensors, every 30 s. Freeways in
  Melbourne only, no arterials or towns, and it needs an account and key on
  that portal: usable later as an open supplement, not as the layer.
- TomTom's Traffic API (chosen): flow as vector tiles, each road's speed as a
  fraction of its free-flow speed, and incidents as vector tiles, both in one
  free pool of 200,000 tile requests a month (docs.tomtom.com/pricing, 22 Nov
  2026), and its Routing API (20,000 free a month) gives a route's travel time
  in live traffic. The owner's key is already on red2 for place hours
  (`MEDIA_TOMTOM_API_KEY`, outside-services row 73).
- HERE's Traffic API v7 (flow and incidents for a box, as lines): its free
  allowance could not be confirmed from HERE's own pages, so not used.

`read(bbox)` answers the `traffic` layer (`GET /map/layer?id=traffic&bbox=`
and `GET /map/traffic?bbox=`, §6.50): each road's flow as a line with
`ratio` (now / free flow), `level` (free, slow, jam, closed) and `colour`,
and each incident as a point with its kind, words and delay. The tiles are
read here, never by the app, so the key stays on the server: the covering
tiles at one zoom, at most MAX_TILES, each kept KEEP_S (TomTom's own
`Expires` is a minute), decoded from Mapbox Vector Tile protobuf by the few
lines below (mapbox-vector-tile, MIT, would bring protobuf and shapely for
this).

`annotate(routes)` gives each driving route `traffic: {duration_s, delay_s,
no_traffic_s}` from TomTom's Routing API asked to follow that very route
(`supportingPoints`, so the road is Valhalla's and only the time is
TomTom's: Valhalla has no live traffic, and TomTom's routing is asked for
the time alone), and puts the fastest in traffic first. `eta(line)` does the
same for what is left of a route being guided, so the app can say "Faster
route available" when a fresh route beats it (POST /map/traffic/eta).

Both stop at a monthly ceiling inside the free tiers, counted per calendar
month in `traffic-usage.json` and checked before each call:
`MEDIA_TOMTOM_TRAFFIC_MONTHLY_MAX` tiles (150,000 of 200,000) and
`MEDIA_TOMTOM_ROUTING_MONTHLY_MAX` routes (15,000 of 20,000); 0 turns either
off. Spent, the layer says `month_spent` and routes keep Valhalla's times.
"""

from __future__ import annotations

import datetime as _dt
import gzip
import hashlib
import json
import math
import os
import struct
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import OrderedDict
from collections.abc import Callable

from agent_media_core import failures as _failures

TILES = "https://api.tomtom.com/traffic/map/4/tile/{kind}/{z}/{x}/{y}.pbf"
ROUTING = "https://api.tomtom.com/routing/1/calculateRoute/{locs}/json"
CREDIT = "Traffic © TomTom"
TIMEOUT_S = 10
#: Tiles read for one box at most; past it, a zoom further out is used.
MAX_TILES = 12
#: The layer is offered from this zoom in (a city at a time).
MIN_ZOOM = 9
#: Flow and incidents kept this long (TomTom's tiles expire in a minute; a
#: few minutes is what Google's layer lags by too).
KEEP_S = 180.0
MOST = 400
#: A route's time in traffic kept this long for the same line.
ETA_KEEP_S = 120.0
#: Points sent to follow a route, at most (TomTom routes between them).
MAX_POINTS = 300
MONTHLY_MAX = {"traffic": 150_000, "routing": 15_000}
USAGE_FILE = "traffic-usage.json"
FLOW_TAGS = "[road_type,traffic_level,road_closure,road_category]"
INCIDENT_TAGS = "[icon_category,description,delay,magnitude,end_date,road_category]"
#: TomTom's incident icon_category, as the app's words.
INCIDENT_KINDS = {0: "unknown", 1: "crash", 2: "fog", 3: "danger", 4: "rain", 5: "ice", 6: "jam",
                  7: "lane_closed", 8: "road_closed", 9: "roadworks", 10: "wind", 11: "flooding",
                  13: "several", 14: "breakdown"}
INCIDENT_TITLES = {"unknown": "Incident", "crash": "Crash", "fog": "Fog", "danger": "Hazard", "rain": "Rain",
                   "ice": "Ice", "jam": "Queue", "lane_closed": "Lane closed", "road_closed": "Road closed",
                   "roadworks": "Roadworks", "wind": "Wind", "flooding": "Flooding", "several": "Incidents",
                   "breakdown": "Broken-down vehicle"}
#: Flow colours as Google's: green, amber, red, and dark red for a closed road.
COLOURS = {"free": "#1e9e4a", "slow": "#f2a51a", "jam": "#d93025", "closed": "#7a0a0a"}
INCIDENT_COLOURS = {"crash": "#d93025", "road_closed": "#7a0a0a", "roadworks": "#f28c1a", "lane_closed": "#f28c1a"}

_CACHE: OrderedDict[tuple, tuple[float, list[dict]]] = OrderedDict()
_ETAS: OrderedDict[str, tuple[float, dict]] = OrderedDict()
_LOCK = threading.Lock()

#: (url, headers) → (status, body bytes). Tests pass their own.
Fetch = Callable[[str, dict], "tuple[int, bytes]"]
#: (url, json body) → the JSON answer. Tests pass their own.
Post = Callable[[str, dict], dict]


def key() -> str:
    return (os.environ.get("MEDIA_TOMTOM_API_KEY") or "").strip()


def enabled() -> bool:
    return bool(key()) and monthly_max("traffic") > 0


def layers() -> list[dict]:
    if not enabled():
        return []
    return [{"id": "traffic", "name": "Traffic", "kind": "flow", "default_on": False, "bbox": True,
             "min_zoom": MIN_ZOOM, "refresh_s": int(KEEP_S), "source": CREDIT,
             "note": "Live: green flowing, amber slow, red queued; crashes and closures as pins."}]


# --- the monthly ceiling ------------------------------------------------------

def monthly_max(service: str) -> int:
    try:
        return int(os.environ.get(f"MEDIA_TOMTOM_{service.upper()}_MONTHLY_MAX") or MONTHLY_MAX[service])
    except ValueError:
        return MONTHLY_MAX[service]


def _state():
    from agent_media_core._paths import state_dir

    return state_dir() / USAGE_FILE


def _month(now: _dt.datetime | None) -> str:
    return (now or _dt.datetime.now(_dt.timezone.utc)).strftime("%Y-%m")


def _read_usage() -> dict:
    try:
        d = json.loads(_state().read_text())
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def usage(service: str, now: _dt.datetime | None = None) -> int:
    """Calls made to `service` ("traffic" tiles, "routing") this calendar month."""
    u = _read_usage()
    calls = u.get("calls") if u.get("month") == _month(now) and isinstance(u.get("calls"), dict) else {}
    return int(calls.get(service) or 0)


def _spend(service: str, n: int = 1, now: _dt.datetime | None = None) -> bool:
    """Count `n` calls to `service`; False (nothing counted) when they would
    pass this month's ceiling, so the calls are not made."""
    with _LOCK:
        m = _month(now)
        u = _read_usage()
        calls = u.get("calls") if u.get("month") == m and isinstance(u.get("calls"), dict) else {}
        have = int(calls.get(service) or 0)
        if have + n > monthly_max(service):
            return False
        p = _state()
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            tmp = p.with_name(f".{p.name}.{os.getpid()}")
            tmp.write_text(json.dumps({"month": m, "calls": {**calls, service: have + n}}))
            os.replace(tmp, p)
        except OSError as e:
            _failures.swallowed(e)
        return True


# --- Mapbox Vector Tiles, read ------------------------------------------------

def _varint(b: bytes, i: int) -> tuple[int, int]:
    out = shift = 0
    while True:
        c = b[i]
        i += 1
        out |= (c & 0x7F) << shift
        if c < 0x80:
            return out, i
        shift += 7


def _fields(b: bytes):
    """(field number, wire type, value) for each field of a protobuf message;
    a length-delimited value is its bytes."""
    i, n = 0, len(b)
    while i < n:
        tag, i = _varint(b, i)
        num, wt = tag >> 3, tag & 7
        if wt == 0:
            v, i = _varint(b, i)
        elif wt == 1:
            v, i = b[i:i + 8], i + 8
        elif wt == 2:
            ln, i = _varint(b, i)
            v, i = b[i:i + ln], i + ln
        elif wt == 5:
            v, i = b[i:i + 4], i + 4
        else:
            raise ValueError(f"wire type {wt}")
        yield num, wt, v


def _packed(b: bytes) -> list[int]:
    out, i = [], 0
    while i < len(b):
        v, i = _varint(b, i)
        out.append(v)
    return out


def _zz(n: int) -> int:
    return (n >> 1) ^ -(n & 1)


def _value(b: bytes):
    for num, _wt, v in _fields(b):
        if num == 1:
            return v.decode("utf-8", "replace")
        if num == 2:
            return struct.unpack("<f", v)[0]
        if num == 3:
            return struct.unpack("<d", v)[0]
        if num == 4:
            return v - (1 << 64) if v >= 1 << 63 else v
        if num == 5:
            return v
        if num == 6:
            return _zz(v)
        if num == 7:
            return bool(v)
    return None


def _geometry(cmds: list[int]) -> list[list[tuple[int, int]]]:
    """The command stream as parts (lines, or a point's single positions)."""
    parts: list[list[tuple[int, int]]] = []
    x = y = i = 0
    while i < len(cmds):
        cmd, count = cmds[i] & 7, cmds[i] >> 3
        i += 1
        if cmd == 7:
            if parts and parts[-1]:
                parts[-1].append(parts[-1][0])
            continue
        for _ in range(count):
            x += _zz(cmds[i])
            y += _zz(cmds[i + 1])
            i += 2
            if cmd == 1:
                parts.append([(x, y)])
            else:
                parts[-1].append((x, y))
    return parts


def decode_mvt(data: bytes) -> dict[str, dict]:
    """A vector tile as `{layer name: {extent, features: [{type, parts, props}]}}`;
    type 1 point, 2 line, 3 polygon; parts in tile coordinates."""
    out: dict[str, dict] = {}
    for num, _wt, lay in _fields(data):
        if num != 3:
            continue
        name, extent, keys, vals, raw = "", 4096, [], [], []
        for n2, _w2, v in _fields(lay):
            if n2 == 1:
                name = v.decode("utf-8", "replace")
            elif n2 == 2:
                raw.append(v)
            elif n2 == 3:
                keys.append(v.decode("utf-8", "replace"))
            elif n2 == 4:
                vals.append(_value(v))
            elif n2 == 5:
                extent = v
        feats = []
        for f in raw:
            typ, tags, geom = 0, [], []
            for n3, _w3, v in _fields(f):
                if n3 == 2:
                    tags = _packed(v)
                elif n3 == 3:
                    typ = v
                elif n3 == 4:
                    geom = _packed(v)
            props = {keys[tags[j]]: vals[tags[j + 1]] for j in range(0, len(tags) - 1, 2)
                     if tags[j] < len(keys) and tags[j + 1] < len(vals)}
            feats.append({"type": typ, "parts": _geometry(geom), "props": props})
        out[name] = {"extent": extent, "features": feats}
    return out


def _lonlat(z: int, x: int, y: int, extent: int, gx: float, gy: float) -> list[float]:
    n = 2 ** z
    lon = (x + gx / extent) / n * 360.0 - 180.0
    lat = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * (y + gy / extent) / n))))
    return [round(lon, 6), round(lat, 6)]


# --- the layer ----------------------------------------------------------------

def tile_of(lon: float, lat: float, z: int) -> tuple[int, int]:
    n = 2 ** z
    lat = max(min(lat, 85.0511), -85.0511)
    x = int((lon + 180.0) / 360.0 * n)
    y = int((1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * n)
    return min(max(x, 0), n - 1), min(max(y, 0), n - 1)


def tiles_for(bbox: tuple[float, float, float, float]) -> tuple[int, list[tuple[int, int]]]:
    """The zoom and the tiles covering the box: about two across, at most MAX_TILES."""
    w, s, e, n = bbox
    span = max(e - w, 1e-6)
    z = min(max(int(math.log2(360.0 / span)) + 1, 5), 16)
    while True:
        x0, y0 = tile_of(w, n, z)
        x1, y1 = tile_of(e, s, z)
        tiles = [(x, y) for x in range(x0, x1 + 1) for y in range(y0, y1 + 1)]
        if len(tiles) <= MAX_TILES or z <= 5:
            return z, tiles[:MAX_TILES]
        z -= 1


def level_of(ratio: float | None, closed: bool) -> str:
    if closed:
        return "closed"
    if ratio is None or ratio >= 0.75:
        return "free"
    return "slow" if ratio >= 0.45 else "jam"


def _get(url: str, headers: dict) -> tuple[int, bytes]:
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as res:
            body = res.read()
            if res.headers.get("Content-Encoding") == "gzip":
                body = gzip.decompress(body)
            return res.status, body
    except urllib.error.HTTPError as e:
        return e.code, b""


def _tile(kind: str, z: int, x: int, y: int, fetch: Fetch | None, now: float,
          spent: list[bool]) -> list[dict] | None:
    """One tile's features as GeoJSON, kept KEEP_S; None when it could not be had."""
    k = (kind, z, x, y)
    with _LOCK:
        hit = _CACHE.get(k)
        if hit and now - hit[0] < KEEP_S:
            _CACHE.move_to_end(k)
            return hit[1]
    if not _spend("traffic"):
        spent.append(True)
        return hit[1] if hit else None
    path = "flow/relative" if kind == "flow" else "incidents"
    q = {"key": key(), "tags": FLOW_TAGS if kind == "flow" else INCIDENT_TAGS}
    if kind == "flow":
        q["margin"] = "0"
    url = TILES.format(kind=path, z=z, x=x, y=y) + "?" + urllib.parse.urlencode(q)
    try:
        status, body = (fetch or _get)(url, {"Accept-Encoding": "gzip", "User-Agent": "agent-media map"})
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        _failures.swallowed(e)
        return hit[1] if hit else None
    if status != 200:
        _failures.swallowed(RuntimeError(f"TomTom {kind} tile: HTTP {status}"))
        return hit[1] if hit else None
    try:
        feats = _flow(z, x, y, body) if kind == "flow" else _incidents(z, x, y, body)
    except (ValueError, IndexError, struct.error) as e:
        _failures.swallowed(e)
        return hit[1] if hit else None
    with _LOCK:
        _CACHE[k] = (now, feats)
        _CACHE.move_to_end(k)
        while len(_CACHE) > MOST:
            _CACHE.popitem(last=False)
    return feats


def _flow(z: int, x: int, y: int, body: bytes) -> list[dict]:
    out = []
    for name, lay in decode_mvt(body).items():
        if "flow" not in name.lower():
            continue
        ext = lay["extent"]
        for f in lay["features"]:
            if f["type"] != 2:
                continue
            p = f["props"]
            ratio = p.get("traffic_level")
            ratio = round(min(max(float(ratio), 0.0), 1.0), 2) if isinstance(ratio, (int, float)) else None
            closed = bool(p.get("road_closure"))
            level = level_of(ratio, closed)
            props = {"level": level, "colour": COLOURS[level], "traffic": True}
            if ratio is not None:
                props["ratio"] = ratio
            if p.get("road_category"):
                props["road"] = p["road_category"]
            for part in f["parts"]:
                if len(part) < 2:
                    continue
                out.append({"type": "Feature", "properties": props,
                            "geometry": {"type": "LineString",
                                         "coordinates": [_lonlat(z, x, y, ext, gx, gy) for gx, gy in part]}})
    return out


def _incidents(z: int, x: int, y: int, body: bytes) -> list[dict]:
    out = []
    for name, lay in decode_mvt(body).items():
        ext = lay["extent"]
        poi = "poi" in name.lower()
        for f in lay["features"]:
            p = f["props"]
            cat = p.get("icon_category", p.get("icon_category_0"))
            kind = INCIDENT_KINDS.get(int(cat or 0), "unknown")
            # Queues show in the flow's colours; an incident's far end is not a second pin.
            if kind == "jam" or p.get("poi_type") == "end_poi":
                continue
            if poi and f["type"] == 1 and f["parts"]:
                gx, gy = f["parts"][0][0]
                if not (0 <= gx <= ext and 0 <= gy <= ext):
                    continue
                delay = int(p["delay"]) if isinstance(p.get("delay"), (int, float)) else None
                words = str(p.get("description") or p.get("description_0") or "")
                detail = " · ".join(x for x in (words, f"{round(delay / 60)} min delay" if delay and delay >= 60 else "",
                                                f"until {p['end_date'][:16].replace('T', ' ')}" if p.get("end_date") else "") if x)
                if p.get("cluster_size"):
                    # Incidents too close to draw apart at this zoom: how many, not TomTom's "Cluster".
                    detail = f"{int(p['cluster_size'])} together; zoom in to see each"
                props = {"title": INCIDENT_TITLES[kind], "detail": detail, "incident": kind,
                         "colour": INCIDENT_COLOURS.get(kind, "#5f6368")}
                if delay is not None:
                    props["delay_s"] = delay
                if p.get("magnitude") is not None:
                    props["magnitude"] = int(p["magnitude"])
                if p.get("cluster_size"):
                    props["count"] = int(p["cluster_size"])
                out.append({"type": "Feature", "properties": props,
                            "geometry": {"type": "Point", "coordinates": _lonlat(z, x, y, ext, gx, gy)}})
            elif not poi and f["type"] == 2 and kind == "road_closed":
                # A closure's own stretch of road, drawn as a closed road.
                for part in f["parts"]:
                    if len(part) >= 2:
                        out.append({"type": "Feature",
                                    "properties": {"level": "closed", "colour": COLOURS["closed"], "traffic": True,
                                                   "closure": True},
                                    "geometry": {"type": "LineString",
                                                 "coordinates": [_lonlat(z, x, y, ext, gx, gy) for gx, gy in part]}})
    return out


def read(bbox, fetch: Fetch | None = None, now: float | None = None):
    """The `traffic` layer for the box: (features, extra), extra saying
    `zoom_in` (too wide), `month_spent`, `partial` (a tile not had), the
    zoom read and the credit."""
    if not enabled():
        return [], {"off": True}
    if bbox is None:
        return [], {"zoom_in": True}
    w, s, e, n = bbox
    if e - w > 360.0 / 2 ** (MIN_ZOOM - 1):
        return [], {"zoom_in": True}
    now = time.time() if now is None else now
    z, tiles = tiles_for(bbox)
    feats: list[dict] = []
    spent: list[bool] = []
    missing = False
    for x, y in tiles:
        for kind in ("flow", "incidents"):
            got = _tile(kind, z, x, y, fetch, now, spent)
            if got is None:
                missing = True
            else:
                feats.extend(got)
    # A point read twice (two tiles' margins): once.
    seen: set[tuple] = set()
    uniq = []
    for f in feats:
        if f["geometry"]["type"] == "Point":
            k = (f["properties"].get("incident"), *f["geometry"]["coordinates"])
            if k in seen:
                continue
            seen.add(k)
        uniq.append(f)
    extra: dict = {"zoom": z, "credit": CREDIT}
    if spent:
        extra["month_spent"] = True
    if missing:
        extra["partial"] = True
    return uniq, extra


# --- routes in traffic --------------------------------------------------------

def _thin(line: list[list[float]], most: int = MAX_POINTS) -> list[list[float]]:
    """At most `most` points of a [[lon, lat], …] line, its ends kept, evenly spread."""
    if len(line) <= most:
        return line
    step = (len(line) - 1) / (most - 1)
    return [line[round(i * step)] for i in range(most)]


def _post(url: str, body: dict) -> dict:
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json", "Accept": "application/json",
                                          "User-Agent": "agent-media map"})
    with urllib.request.urlopen(req, timeout=TIMEOUT_S) as res:
        return json.loads(res.read().decode("utf-8") or "{}")


def eta(line: list[list[float]], post: Post | None = None, now: float | None = None) -> dict | None:
    """The time along `line` ([[lon, lat], …], a car's way) in today's traffic:
    `{duration_s, delay_s, no_traffic_s}`, or None (off, spent, too short,
    or TomTom did not answer)."""
    if not key() or monthly_max("routing") <= 0:
        return None
    pts = [p for p in line if isinstance(p, (list, tuple)) and len(p) >= 2]
    if len(pts) < 2:
        return None
    pts = _thin(pts)
    now = time.time() if now is None else now
    h = hashlib.sha1(json.dumps([[round(p[0], 4), round(p[1], 4)] for p in pts]).encode()).hexdigest()
    with _LOCK:
        hit = _ETAS.get(h)
        if hit and now - hit[0] < ETA_KEEP_S:
            return hit[1]
    if not _spend("routing"):
        return None
    a, b = pts[0], pts[-1]
    locs = f"{a[1]:.6f},{a[0]:.6f}:{b[1]:.6f},{b[0]:.6f}"
    q = {"key": key(), "traffic": "true", "computeTravelTimeFor": "all", "travelMode": "car",
         "routeRepresentation": "summaryOnly"}
    url = ROUTING.format(locs=locs) + "?" + urllib.parse.urlencode(q)
    try:
        got = (post or _post)(url, {"supportingPoints": [{"latitude": p[1], "longitude": p[0]} for p in pts]})
        s = (got.get("routes") or [{}])[0].get("summary") or {}
        out = {"duration_s": int(s["travelTimeInSeconds"]), "delay_s": int(s.get("trafficDelayInSeconds") or 0)}
        if s.get("noTrafficTravelTimeInSeconds") is not None:
            out["no_traffic_s"] = int(s["noTrafficTravelTimeInSeconds"])
    except (urllib.error.URLError, TimeoutError, OSError, ValueError, KeyError, TypeError, IndexError) as e:
        _failures.swallowed(e)
        return None
    with _LOCK:
        _ETAS[h] = (now, out)
        while len(_ETAS) > MOST:
            _ETAS.popitem(last=False)
    return out


def annotate(routes: list[dict], post: Post | None = None, now: float | None = None) -> list[dict]:
    """Driving routes with their time in traffic (`traffic`, plus `credit`),
    the fastest in traffic first; routes TomTom could not time keep
    Valhalla's order behind those it could."""
    if not routes or not key():
        return routes
    for r in routes:
        if r.get("mode") != "drive" or not r.get("line"):
            continue
        t = eta(r["line"], post=post, now=now)
        if t:
            r["traffic"] = {**t, "credit": CREDIT}
    timed = [r for r in routes if r.get("traffic")]
    if len(timed) < 2:
        return routes
    return sorted(routes, key=lambda r: (0, r["traffic"]["duration_s"]) if r.get("traffic") else (1, 0))


def _reset_for_tests() -> None:
    with _LOCK:
        _CACHE.clear()
        _ETAS.clear()
