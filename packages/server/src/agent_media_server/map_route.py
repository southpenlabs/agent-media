"""Directions for the Map (maps proposal step 6; David, 17 Nov 2026).

`GET /map/route?from=lat,lon&to=lat,lon[&mode=walk|drive|bike][&lang=en-AU]`
asks a Valhalla routing service (`MEDIA_ROUTER_URL`, e.g.
http://127.0.0.1:8002; open source, MIT, run by the owner) and answers
compactly: the line, the distance and time, and each turn with Valhalla's
own words to speak before it and as it comes (`verbal_pre_transition_
instruction`, `verbal_transition_alert_instruction`), so the app can say
them as you reach each one. 404 `no_router` when none is set.

`GET /map/search?q=<words>[&near=lat,lon]` finds a place for the Map's
search box (core place_find: Nominatim), nearest first when `near` is given.
"""

from __future__ import annotations

import json
import os
import urllib.request

MODES = {"walk": "pedestrian", "drive": "auto", "bike": "bicycle"}
TIMEOUT_S = 20


def router_url() -> str:
    return (os.environ.get("MEDIA_ROUTER_URL") or "").rstrip("/")


def decode_polyline(s: str, precision: int = 6) -> list[list[float]]:
    """Valhalla's encoded shape (polyline6) as [[lon, lat], …]."""
    out, i, lat, lon, f = [], 0, 0, 0, 10 ** precision
    while i < len(s):
        vals = []
        for _ in range(2):
            shift = res = 0
            while True:
                b = ord(s[i]) - 63
                i += 1
                res |= (b & 0x1F) << shift
                shift += 5
                if b < 0x20:
                    break
            vals.append(~(res >> 1) if res & 1 else res >> 1)
        lat += vals[0]
        lon += vals[1]
        out.append([lon / f, lat / f])
    return out


def _point(raw: str) -> tuple[float, float] | None:
    try:
        lat, lon = (float(x) for x in (raw or "").split(","))
    except ValueError:
        return None
    return (lat, lon) if -90 <= lat <= 90 and -180 <= lon <= 180 else None


def route(frm: str, to: str, mode: str = "drive", lang: str = "") -> tuple[bool, dict]:
    base = router_url()
    if not base:
        return False, {"status": 404, "code": "no_router", "error": "no routing service on this server"}
    a, b = _point(frm), _point(to)
    if not a or not b:
        return False, {"status": 400, "error": "from and to are lat,lon"}
    costing = MODES.get(mode or "drive")
    if not costing:
        return False, {"status": 400, "error": "mode is walk, drive or bike"}
    q = {"locations": [{"lat": a[0], "lon": a[1]}, {"lat": b[0], "lon": b[1]}], "costing": costing,
         "directions_options": {"units": "kilometers", "language": lang or "en-AU"}}
    req = urllib.request.Request(f"{base}/route", data=json.dumps(q).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as res:
            got = json.loads(res.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as e:
        try:
            err = json.loads(e.read().decode("utf-8") or "{}")
        except ValueError:
            err = {}
        return False, {"status": 404, "code": "no_route", "error": err.get("error") or "no route between these"}
    except Exception as e:  # noqa: BLE001
        return False, {"status": 502, "error": f"the routing service did not answer: {e.__class__.__name__}"}
    trip = got.get("trip") or {}
    line: list[list[float]] = []
    steps = []
    for leg in trip.get("legs") or []:
        shape = decode_polyline(leg.get("shape") or "")
        base_i = len(line)
        line.extend(shape)
        for m in leg.get("maneuvers") or []:
            at = shape[m.get("begin_shape_index", 0)] if shape else None
            steps.append({
                "instruction": m.get("instruction") or "",
                "say_before": m.get("verbal_pre_transition_instruction") or m.get("instruction") or "",
                "say_alert": m.get("verbal_transition_alert_instruction") or "",
                "say_after": m.get("verbal_post_transition_instruction") or "",
                "street": ", ".join(m.get("street_names") or []),
                "distance_m": round((m.get("length") or 0) * 1000),
                "time_s": round(m.get("time") or 0),
                "type": m.get("type"),
                "at": at,
                "index": base_i + (m.get("begin_shape_index") or 0),
            })
    s = trip.get("summary") or {}
    return True, {"mode": mode or "drive", "distance_m": round((s.get("length") or 0) * 1000),
                  "duration_s": round(s.get("time") or 0), "line": line, "steps": steps}


def search(q: str, near: str = "") -> tuple[bool, dict]:
    from agent_media_core import place_find

    words = " ".join((q or "").split())
    if not words:
        return False, {"status": 400, "error": "say what to find"}
    pt = _point(near) if near else None
    try:
        rows = place_find.find(words, near=pt, limit=8)
    except Exception as e:  # noqa: BLE001
        return False, {"status": 502, "error": f"the place search did not answer: {e.__class__.__name__}"}
    if pt:
        import math

        def dist(r):
            kx = math.cos(math.radians(pt[0])) * 111_320.0
            return math.hypot((r["lon"] - pt[1]) * kx, (r["lat"] - pt[0]) * 110_540.0)
        rows.sort(key=dist)
    return True, {"places": rows}
