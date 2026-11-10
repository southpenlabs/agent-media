"""The app's Map page: which layers there are, and each layer's features
(docs/proposals/2026-11-06-maps.md, step 1).

`GET /map/layers` lists the layers; `GET /map/layer?id=<id>[&bbox=w,s,e,n]`
answers one as a GeoJSON FeatureCollection. The app draws what it is sent
and knows nothing of where a layer comes from (the app stays generic).

Built in: `phone`, the phone's shared location (§6.21b), the freshest fix
per phone with its accuracy and age; and `thread:<session>`, the places a
chat's replies pinned with `[[map:]]` / `[[route:]]` (maps proposal step 2),
listed by `GET /map/layers?thread=<session>` and read like any other. More layers come from add-ons through
the `agent_media.map_layers` entry point: each names a callable returning
`[{"layer": {...}, "features": callable(bbox) -> [feature, ...]}]`, so a
region's cameras or a house's rooms live outside this repo.

Location is the owner's: a guest device is refused (403), as it may read
chats and listen but has no business knowing where the phone is.
"""

from __future__ import annotations

import logging
import os
import time
from importlib.metadata import entry_points

from . import auth, phone

log = logging.getLogger(__name__)

ENTRY_GROUP = "agent_media.map_layers"

_PHONE = {"id": "phone", "name": "My phone", "kind": "points", "icon": "phone",
          "default_on": True, "refresh_s": 30, "private": True}


def _phone_features(_bbox) -> list[dict]:
    now = time.time()
    out = []
    for f in phone.shared_all():
        props = {"title": f.get("device") or "My phone", "at": f["at"],
                 "age_s": round(max(0.0, now - f["at"])), "precise": f["precise"]}
        if "accuracy_m" in f:
            props["accuracy_m"] = f["accuracy_m"]
        out.append({"type": "Feature", "properties": props,
                    "geometry": {"type": "Point", "coordinates": [f["lon"], f["lat"]]}})
    return out


#: The newest this many messages of a chat are searched for its places.
THREAD_MESSAGES = 2000


def _thread_id(layer_id: str) -> str:
    """The session id in `thread:<session>`, or ""."""
    from . import sessions

    sid = layer_id.removeprefix("thread:") if layer_id.startswith("thread:") else ""
    return sid if sessions._SESSION.fullmatch(sid) else ""


def thread_places(session: str) -> list[dict]:
    """Every place a chat's replies pinned, oldest first: GeoJSON features, a
    Point per place (`title`, `msg`, `link`, `group`) and a LineString per
    route."""
    from . import transcript

    got = transcript.messages(session, limit=THREAD_MESSAGES)
    if not got:
        return []
    msgs = got[0]
    transcript.strip_markers(msgs)
    out: list[dict] = []
    n = 0
    for m in msgs:
        for g in m.get("places") or []:
            n += 1
            pts = g.get("points") or []
            if g.get("kind") == "route" and len(pts) > 1:
                out.append({"type": "Feature",
                            "properties": {"title": " → ".join(p["name"] for p in pts if p["name"]),
                                           "msg": m.get("id"), "link": f"/t/{session}", "group": n,
                                           "route": True},
                            "geometry": {"type": "LineString",
                                         "coordinates": [[p["lon"], p["lat"]] for p in pts]}})
            for p in pts:
                out.append({"type": "Feature",
                            "properties": {"title": p["name"], "msg": m.get("id"),
                                           "link": f"/t/{session}", "group": n},
                            "geometry": {"type": "Point", "coordinates": [p["lon"], p["lat"]]}})
    return out


def _thread_layer(session: str) -> dict:
    return {"layer": {"id": f"thread:{session}", "name": "Places in this chat", "kind": "points",
                      "icon": "pin", "default_on": True},
            "features": lambda _bbox: thread_places(session)}


def _addons() -> list[dict]:
    found = []
    for ep in entry_points(group=ENTRY_GROUP):
        try:
            for item in ep.load()() or []:
                lay = item.get("layer") or {}
                if lay.get("id") and callable(item.get("features")):
                    found.append(item)
        except Exception:  # an add-on's fault must not take the map down
            log.exception("map layer add-on %s failed", ep.name)
    return found


def _all() -> list[dict]:
    from . import map_osm

    out = [{"layer": _PHONE, "features": _phone_features}]
    # OpenStreetMap's cameras (map_osm.py): read for the box on screen.
    for lay in map_osm.layers():
        out.append({"layer": lay, "features": (lambda lid: lambda bbox: map_osm.read(lid, bbox))(lay["id"])})
    # Victoria's phone and seatbelt camera sites (map_vic.py), where switched on.
    from . import map_vic

    for lay in map_vic.layers():
        out.append({"layer": lay, "features": map_vic.read})
    seen = {i["layer"]["id"] for i in out}
    for item in _addons():
        if item["layer"]["id"] not in seen:
            seen.add(item["layer"]["id"])
            out.append(item)
    return out


def _gate(bearer: str, host: bool = False) -> tuple[bool, dict]:
    if host:
        return True, {}
    user, err = auth.gate(bearer)
    if not user:
        return False, err
    if auth.is_guest(bearer):
        return False, {"status": 403, "code": "guest",
                       "error": "a guest device does not see the map"}
    return True, {}


def _bbox(raw: str):
    """`w,s,e,n` as four floats, or None when absent or not that."""
    if not raw:
        return None
    try:
        w, s, e, n = (float(x) for x in raw.split(","))
    except ValueError:
        return None
    if not (-180 <= w <= 180 and -180 <= e <= 180 and -90 <= s <= n <= 90):
        return None
    return (w, s, e, n)


def _inside(feature: dict, bbox) -> bool:
    geom = feature.get("geometry") or {}
    if bbox is None or geom.get("type") != "Point":
        return True
    lon, lat = geom["coordinates"][:2]
    w, s, e, n = bbox
    in_lon = w <= lon <= e if w <= e else (lon >= w or lon <= e)  # across 180
    return in_lon and s <= lat <= n


def layers(bearer: str, thread: str = "", host: bool = False) -> tuple[bool, dict]:
    ok, err = _gate(bearer, host)
    if not ok:
        return False, err
    items = _all()
    if thread:
        sid = _thread_id(f"thread:{thread}")
        if not sid:
            return False, {"status": 400, "error": "not a session id"}
        items = [_thread_layer(sid), *items]
    return True, {"layers": [dict(i["layer"]) for i in items]}


def layer(layer_id: str, bbox_raw: str, bearer: str, host: bool = False) -> tuple[bool, dict]:
    ok, err = _gate(bearer, host)
    if not ok:
        return False, err
    sid = _thread_id(layer_id)
    item = (_thread_layer(sid) if sid else
            next((i for i in _all() if i["layer"]["id"] == layer_id), None))
    if item is None:
        return False, {"status": 404, "error": "no such layer"}
    bbox = _bbox(bbox_raw)
    try:
        got = item["features"](bbox)
        # A source may answer (features, extra): extra's keys (as_of, partial,
        # zoom_in) go on the collection.
        got, extra = got if isinstance(got, tuple) else (got, {})
        feats = [f for f in got or [] if _inside(f, bbox)]
    except Exception:
        log.exception("map layer %s failed", layer_id)
        return False, {"status": 502, "error": "that layer could not be read"}
    return True, {**extra, "type": "FeatureCollection", "layer": layer_id,
                  "at": round(time.time(), 3), "features": feats}


#: The cameras the phone is handed for its alerts, around where it is.
ALERT_RADIUS_KM = 25.0


def alerts(near: str, radius_km: str, bearer: str, host: bool = False) -> tuple[bool, dict]:
    """`GET /map/alerts?near=lat,lon[&radius_km=]`: the cameras the phone
    alerts on as it drives (David, 10 Nov 2026: "alerts for when approaching
    them"), compact: `points` (speed, red light, average speed cameras with
    `kind` and `limit`) and `roads` (Victoria's phone and seatbelt sites,
    where on, each a name and its lines)."""
    import math

    from . import map_osm, map_vic

    ok, err = _gate(bearer, host)
    if not ok:
        return False, err
    try:
        lat, lon = (float(x) for x in (near or "").split(","))
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            raise ValueError
    except ValueError:
        return False, {"status": 400, "error": "near is lat,lon"}
    try:
        r = min(max(float(radius_km or ALERT_RADIUS_KM), 1.0), 50.0)
    except ValueError:
        r = ALERT_RADIUS_KM
    dlat = r / 111.0
    dlon = r / (111.0 * max(math.cos(math.radians(lat)), 0.05))
    box = (lon - dlon, lat - dlat, lon + dlon, lat + dlat)
    points, roads = [], []
    if any(l["id"] == "speed_cameras" for l in map_osm.layers()):
        if os.environ.get("MEDIA_MAP_OSM_EXTRACT"):
            feats, _x = map_osm._from_extract("speed_cameras", box)
        else:
            feats, _x = map_osm.read("speed_cameras", box)
        for f in feats:
            p = f.get("properties") or {}
            if p.get("enforces", "speed") not in ("speed", "red_light", "average_speed", "phone", "seatbelt"):
                continue
            x, y = f["geometry"]["coordinates"][:2]
            points.append({"lat": y, "lon": x, "kind": p.get("enforces", "speed"),
                           **({"limit": p["limit"]} if p.get("limit") else {})})
    if map_vic.enabled():
        feats, _x = map_vic.read(box)
        for f in feats:
            g = f["geometry"]
            lines = ([g["coordinates"]] if g["type"] == "LineString"
                     else g["coordinates"] if g["type"] == "MultiLineString"
                     else [[g["coordinates"], g["coordinates"]]])
            name = ((f.get("properties") or {}).get("detail") or "").split(" (")[0]
            roads.append({"name": name.split(",")[0], "where": name, "kind": "phone_seatbelt",
                          "lines": lines})
    return True, {"at": round(time.time(), 3), "near": [lat, lon], "radius_km": r,
                  "points": points, "roads": roads}
