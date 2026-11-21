"""Directions for the Map (maps proposal step 6; David, 17 Nov 2026).

`GET /map/route?from=lat,lon&to=lat,lon[&via=lat,lon;lat,lon…][&mode=walk|drive|bike|transit]
[&avoid=tolls,highways,ferries][&alternates=0..2][&depart=YYYY-MM-DDTHH:MM | &arrive=…][&lang=en-AU]`
asks a Valhalla routing service (`MEDIA_ROUTER_URL`, e.g.
http://127.0.0.1:8002; open source, MIT, run by the owner) and answers
compactly: the line, the distance and time, and each turn with Valhalla's
own words to speak before it and as it comes (`verbal_pre_transition_
instruction`, `verbal_transition_alert_instruction`), so the app can say
them as you reach each one. 404 `no_router` when none is set.

The route planner's options are Google Maps' (David, 19 Nov 2026): avoid
tolls, highways or ferries (Valhalla's costing_options use_* 0), up to two
alternative routes (Valhalla's `alternates`, two-point routes only), stops on
the way (`via`, each a "break" location, so each makes a leg), leave at or
arrive by (Valhalla's `date_time`), and public transport (costing
"multimodal", on transit tiles built from the operator's GTFS; 404
`no_transit` where the service has none). The top level keeps the first
route's fields as before; `routes` has every route, chosen first.

`GET /map/route/modes` lists the modes and avoids this server can do, Transit
only when its Valhalla has transit tiles (its verbose /status, kept 30 min).

`GET /map/search?q=<words>[&near=lat,lon]` finds a place for the Map's
search box (core place_find: Nominatim), nearest first when `near` is given.
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import re
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from agent_media_core import failures as _failures

MODES = {"walk": "pedestrian", "drive": "auto", "bike": "bicycle", "transit": "multimodal"}
AVOIDS = ("tolls", "highways", "ferries")
MAX_VIA = 8
MAX_ALTERNATES = 2
TIMEOUT_S = 20
TRANSIT_CHECK_S = 30 * 60
# Valhalla's travel_type for a transit maneuver, as the app's kind.
TRANSIT_KINDS = {"tram": "tram", "metro": "train", "rail": "train", "bus": "bus", "ferry": "ferry",
                 "cable_car": "cable_car", "gondola": "gondola", "funicular": "funicular"}
_REF = re.compile(r"^[A-Z]{0,3}\d+[A-Z]?$")
_TIME = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$")
_transit_seen: dict[str, tuple[float, bool]] = {}


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


def _bad(code: str, error: str) -> tuple[bool, dict]:
    return False, {"status": 400, "code": code, "error": error}


def _ask(base: str, path: str, body: dict | None = None, timeout: float = TIMEOUT_S) -> dict:
    """POST `body` (or GET when None) to the routing service; its JSON answer."""
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(f"{base}{path}", data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as res:
        return json.loads(res.read().decode("utf-8") or "{}")


def has_transit(base: str | None = None, clock: Callable[[], float] = time.monotonic) -> bool:
    """Whether this server's Valhalla has transit tiles: its verbose /status
    (`has_transit_tiles`; the build turns allow_verbose on; it also maps the
    tiles' extent, so it is not cheap), asked at most once
    per 30 min (a failed ask once a minute)."""
    base = router_url() if base is None else base
    if not base:
        return False
    now = clock()
    seen = _transit_seen.get(base)
    if seen and now < seen[0]:
        return seen[1]
    try:
        # Verbose is asked in the body: Valhalla ignores ?verbose=true.
        got = bool(_ask(base, "/status", {"verbose": True}, timeout=10).get("has_transit_tiles"))
        _transit_seen[base] = (now + TRANSIT_CHECK_S, got)
    except Exception as e:  # noqa: BLE001
        _failures.swallowed(e)
        got = False
        _transit_seen[base] = (now + 60, got)
    return got


def modes(base: str | None = None) -> tuple[bool, dict]:
    """`GET /map/route/modes`: what the route planner can do here."""
    base = router_url() if base is None else base
    if not base:
        return False, {"status": 404, "code": "no_router", "error": "no routing service on this server"}
    out = ["walk", "drive", "bike"] + (["transit"] if has_transit(base) else [])
    return True, {"modes": out, "avoid": list(AVOIDS), "max_via": MAX_VIA, "max_alternates": MAX_ALTERNATES}


def _when(raw: str) -> _dt.datetime | None:
    if not _TIME.match(raw or ""):
        return None
    try:
        return _dt.datetime.strptime(raw, "%Y-%m-%dT%H:%M")
    except ValueError:
        return None


def _color(raw) -> str | None:
    if raw is None or raw == "":
        return None
    try:
        n = int(raw) if not isinstance(raw, str) else int(raw.lstrip("#"), 16)
    except ValueError:
        return None
    return f"#{n & 0xFFFFFF:06x}"


def _transit(m: dict, ti: dict) -> dict:
    stops = ti.get("transit_stops") or []
    kind = m.get("travel_type") or ""
    out = {"kind": TRANSIT_KINDS.get(kind, kind or "transit"),
           "route_name": ti.get("short_name") or "",
           "route_long_name": ti.get("long_name") or "",
           "headsign": ti.get("headsign") or "",
           "operator": ti.get("operator_name") or "",
           "departs": (stops[0].get("departure_date_time") or None) if stops else None,
           "arrives": (stops[-1].get("arrival_date_time") or None) if stops else None,
           "stops": max(len(stops) - 1, 0),
           "from_stop": (stops[0].get("name") or "") if stops else "",
           "to_stop": (stops[-1].get("name") or "") if stops else ""}
    color = _color(ti.get("color"))
    if color:
        out["color"] = color
    return out


def _label(steps: list[dict]) -> str:
    """A few words telling one route from another: its lines for transit,
    else the street it spends longest on ("via Sydney Road")."""
    lines = [s["transit"] for s in steps if s.get("transit")]
    if lines:
        return ", ".join(" ".join(x for x in (t["kind"].replace("_", " ").capitalize(), t["route_name"]) if x)
                         for t in lines)
    far: dict[str, int] = {}
    for s in steps:
        # A road's name, not its route number: "55, Royal Parade" is Royal Parade.
        names = [n for n in (s.get("street") or "").split(", ") if n and not _REF.match(n)]
        if names:
            far[names[0]] = far.get(names[0], 0) + s["distance_m"]
    return f"via {max(far, key=lambda k: far[k])}" if far else ""


def _trip(trip: dict, mode: str) -> dict:
    line: list[list[float]] = []
    steps: list[dict] = []
    legs: list[dict] = []
    for li, leg in enumerate(trip.get("legs") or []):
        shape = decode_polyline(leg.get("shape") or "")
        base_i = len(line)
        line.extend(shape)
        for m in leg.get("maneuvers") or []:
            at = shape[min(m.get("begin_shape_index", 0), len(shape) - 1)] if shape else None
            step = {
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
                "leg": li,
            }
            if m.get("transit_info"):
                step["transit"] = _transit(m, m["transit_info"])
            steps.append(step)
        ls = leg.get("summary") or {}
        legs.append({"distance_m": round((ls.get("length") or 0) * 1000), "duration_s": round(ls.get("time") or 0)})
    s = trip.get("summary") or {}
    out = {"mode": mode, "distance_m": round((s.get("length") or 0) * 1000),
           "duration_s": round(s.get("time") or 0), "line": line, "steps": steps, "legs": legs,
           "has_toll": bool(s.get("has_toll")), "has_highway": bool(s.get("has_highway")),
           "has_ferry": bool(s.get("has_ferry"))}
    label = _label(steps)
    if label:
        out["summary_label"] = label
    return out


def _times(trip: dict, out: dict, depart: _dt.datetime | None, arrive: _dt.datetime | None) -> None:
    """depart_at / arrive_at: Valhalla's own date_time on the first and last
    location when it gives them (it fills in the far end when the timezones
    are known), else the asked time plus or minus the trip's duration."""
    locs = trip.get("locations") or []
    first = (locs[0].get("date_time") if locs else None) or None
    last = (locs[-1].get("date_time") if locs else None) or None
    dur = _dt.timedelta(seconds=out["duration_s"])
    fmt = "%Y-%m-%dT%H:%M"
    if depart:
        first = first or depart.strftime(fmt)
        last = last or (depart + dur).strftime(fmt)
    elif arrive:
        last = last or arrive.strftime(fmt)
        first = first or (arrive - dur).strftime(fmt)
    else:
        return
    out["depart_at"], out["arrive_at"] = first, last
    tz = next((loc.get("time_zone_name") for loc in locs if loc.get("time_zone_name")), None)
    if tz:
        out["time_zone"] = tz


def route(frm: str, to: str, mode: str = "drive", lang: str = "", via: str = "", avoid: str = "",
          alternates: str = "", depart: str = "", arrive: str = "", base: str | None = None) -> tuple[bool, dict]:
    base = router_url() if base is None else base
    if not base:
        return False, {"status": 404, "code": "no_router", "error": "no routing service on this server"}
    a, b = _point(frm), _point(to)
    if not a or not b:
        return _bad("bad_point", "from and to are lat,lon")
    mode = mode or "drive"
    costing = MODES.get(mode)
    if not costing:
        return _bad("bad_mode", "mode is walk, drive, bike or transit")
    stops: list[tuple[float, float]] = []
    for raw in (via or "").split(";"):
        if not raw.strip():
            continue
        pt = _point(raw.strip())
        if not pt:
            return _bad("bad_via", "via is lat,lon;lat,lon…")
        stops.append(pt)
    if len(stops) > MAX_VIA:
        return _bad("too_many_via", f"at most {MAX_VIA} stops on the way")
    avoids = [x.strip().lower() for x in (avoid or "").split(",") if x.strip()]
    if any(x not in AVOIDS for x in avoids):
        return _bad("bad_avoid", "avoid is one or more of tolls, highways, ferries")
    try:
        n_alt = int(alternates) if (alternates or "").strip() else 0
    except ValueError:
        n_alt = -1
    if not 0 <= n_alt <= MAX_ALTERNATES:
        return _bad("bad_alternates", f"alternates is 0 to {MAX_ALTERNATES}")
    if depart and arrive:
        return _bad("depart_and_arrive", "leave at or arrive by, not both")
    when_d, when_a = _when(depart) if depart else None, _when(arrive) if arrive else None
    if (depart and not when_d) or (arrive and not when_a):
        return _bad("bad_time", "depart and arrive are local times, YYYY-MM-DDTHH:MM")
    if mode == "transit" and not has_transit(base):
        return False, {"status": 404, "code": "no_transit", "error": "no public transport timetables on this server"}

    locs = [{"lat": a[0], "lon": a[1]}] + [{"lat": p[0], "lon": p[1], "type": "break"} for p in stops] \
        + [{"lat": b[0], "lon": b[1]}]
    q: dict = {"locations": locs, "costing": costing,
               "directions_options": {"units": "kilometers", "language": lang or "en-AU"}}
    opts: dict = {}
    if costing == "auto":
        opts = {k: 0 for x, k in (("tolls", "use_tolls"), ("highways", "use_highways"), ("ferries", "use_ferry"))
                if x in avoids}
    elif "ferries" in avoids:
        opts = {"use_ferry": 0}
    if opts:
        # Transit walks between stops: the ferry avoid is the walking part's.
        q["costing_options"] = {("pedestrian" if costing == "multimodal" else costing): opts}
    if n_alt and not stops:
        q["alternates"] = n_alt
    if when_d or when_a:
        q["date_time"] = {"type": 1 if when_d else 2, "value": depart or arrive}
    try:
        got = _ask(base, "/route", q)
    except urllib.error.HTTPError as e:
        try:
            err = json.loads(e.read().decode("utf-8") or "{}")
        except ValueError:
            err = {}
        if err.get("error_code") == 141:
            return _bad("arrive_unsupported", err.get("error") or "arrive by is not offered for this mode")
        if 160 <= (err.get("error_code") or 0) <= 163:
            return _bad("bad_time", err.get("error") or "the routing service refused that time")
        return False, {"status": 404, "code": "no_route", "error": err.get("error") or "no route between these"}
    except Exception as e:  # noqa: BLE001
        _failures.swallowed(e)
        return False, {"status": 502, "error": f"the routing service did not answer: {e.__class__.__name__}"}
    trips = [got.get("trip") or {}] + [(x or {}).get("trip") or {} for x in got.get("alternates") or []]
    routes = []
    for t in trips:
        r = _trip(t, mode)
        _times(t, r, when_d, when_a)
        routes.append(r)
    first = routes[0]
    out = {k: first[k] for k in ("mode", "distance_m", "duration_s", "line", "steps", "legs")}
    for k in ("depart_at", "arrive_at", "time_zone"):
        if k in first:
            out[k] = first[k]
    out["routes"] = routes
    return True, out

def search(q: str, near: str = "") -> tuple[bool, dict]:
    from agent_media_core import place_find

    words = " ".join((q or "").split())
    if not words:
        return False, {"status": 400, "error": "say what to find"}
    pt = _point(near) if near else None
    try:
        rows = place_find.find(words, near=pt, limit=8)
    except Exception as e:  # noqa: BLE001
        _failures.swallowed(e)
        return False, {"status": 502, "error": f"the place search did not answer: {e.__class__.__name__}"}
    if pt:
        import math

        def dist(r):
            kx = math.cos(math.radians(pt[0])) * 111_320.0
            return math.hypot((r["lon"] - pt[1]) * kx, (r["lat"] - pt[0]) * 110_540.0)
        rows.sort(key=dist)
        for r in rows:
            r["distance_m"] = round(dist(r))
    # Open or not, and until when, as /map/places has it (map_places.open_state).
    from . import map_places

    for r in rows:
        if r.get("opening_hours"):
            r["open_now"], changes = map_places.open_state(r["opening_hours"], r["lat"], r["lon"])
            if changes:
                r["changes_at"] = changes
    return True, {"places": rows}
