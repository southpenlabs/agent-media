"""Places near a point or in a box, from OpenStreetMap through Overpass
(David, 19 Nov 2026: "Build the places endpoint"): the nearest open
pharmacy, fuel near me, a café on this walk, EV chargers, toilets, water.

`GET /map/places?near=lat,lon[&radius_m=1500] | bbox=w,s,e,n
| along=lat,lon;lat,lon;…[&radius_m=500]
&kind=<kind>[,<kind>][&open_now=1][&limit=20]` (§6.50). `along` is a
route ahead (David, 21 Nov 2026: "add search along route"): places within
`radius_m` of the line, in the order the route reaches them. The client names
kinds from the fixed list below and never sends Overpass text: each kind is
one or more tag filters here, and the query is built from them. A kind may
also be named by an alias ("chemist", "petrol", "loo"), so an agent's words
can go straight in.

Overpass is asked through map_osm.overpass (the same `MEDIA_OVERPASS_URL`
list, tried in order; on red2 its own Overpass for Australia first, a public
one after). An answer is kept in memory for KEEP_S by its kinds and its
rounded circle or box, so a phone moving a few metres asks nothing new;
the distances, the order and `open_now` are worked out afresh each time.

`open_now` reads the place's OSM `opening_hours` with opening-hours-py
(Rémi Dupré's opening-hours-rs, MIT OR Apache-2.0): the whole syntax,
evaluated in the place's own timezone with its country's public holidays,
both found from its position. Null when a place has no hours, they cannot
be read, or they say "unknown". OSM data © OpenStreetMap contributors, ODbL.
"""

from __future__ import annotations

import datetime as dt
import math
import re
import threading
import time
from typing import Callable

from agent_media_core import failures as _failures

from . import map_osm

#: kind → its name and the Overpass tag filters any one of which makes a
#: place that kind. The only Overpass text a request can cause.
KINDS: dict[str, dict] = {
    "pharmacy": {"name": "Pharmacy", "filters": ['["amenity"="pharmacy"]', '["healthcare"="pharmacy"]'],
                 "aliases": ["chemist", "drugstore"]},
    "cafe": {"name": "Café", "filters": ['["amenity"="cafe"]'], "aliases": ["café", "coffee"]},
    "restaurant": {"name": "Restaurant", "filters": ['["amenity"="restaurant"]'], "aliases": []},
    "fast_food": {"name": "Fast food", "filters": ['["amenity"="fast_food"]'], "aliases": ["takeaway"]},
    "fuel": {"name": "Fuel", "filters": ['["amenity"="fuel"]'],
             "aliases": ["petrol", "petrol station", "gas station", "servo", "service station"]},
    "ev_charger": {"name": "EV charger", "filters": ['["amenity"="charging_station"]'],
                   "aliases": ["ev", "charger", "charging station", "ev charging"]},
    "toilets": {"name": "Toilets", "filters": ['["amenity"="toilets"]'],
                "aliases": ["toilet", "loo", "restroom", "public toilet"]},
    "drinking_water": {"name": "Drinking water", "filters": ['["amenity"="drinking_water"]'],
                       "aliases": ["water", "water fountain", "bubbler", "tap"]},
    "supermarket": {"name": "Supermarket", "filters": ['["shop"="supermarket"]'],
                    "aliases": ["grocery", "groceries"]},
    "convenience": {"name": "Convenience store", "filters": ['["shop"="convenience"]'],
                    "aliases": ["convenience store", "milk bar"]},
    "atm": {"name": "ATM", "filters": ['["amenity"="atm"]', '["atm"="yes"]'], "aliases": ["cash"]},
    "parking": {"name": "Parking", "filters": ['["amenity"="parking"]'], "aliases": ["car park", "carpark"]},
    "hospital": {"name": "Hospital", "filters": ['["amenity"="hospital"]'], "aliases": ["emergency"]},
    "bakery": {"name": "Bakery", "filters": ['["shop"="bakery"]'], "aliases": []},
    "bar": {"name": "Bar", "filters": ['["amenity"="bar"]'], "aliases": []},
    "pub": {"name": "Pub", "filters": ['["amenity"="pub"]'], "aliases": ["hotel pub"]},
    "playground": {"name": "Playground", "filters": ['["leisure"="playground"]'], "aliases": []},
    "viewpoint": {"name": "Lookout", "filters": ['["tourism"="viewpoint"]'], "aliases": ["lookout"]},
    "bus_stop": {"name": "Bus stop", "filters": ['["highway"="bus_stop"]'], "aliases": ["bus"]},
    "tram_stop": {"name": "Tram stop", "filters": ['["railway"="tram_stop"]'], "aliases": ["tram"]},
    "train_station": {"name": "Train station",
                      "filters": ['["railway"="station"]["station"!="subway"]', '["railway"="halt"]'],
                      "aliases": ["train", "station", "railway station"]},
}

DEFAULT_RADIUS_M = 1500
MAX_RADIUS_M = 50_000
#: A box's sides at most, in degrees (about 55 km north-south).
MAX_BOX_DEG = 0.5
DEFAULT_LIMIT = 20
MAX_LIMIT = 100
#: Elements Overpass is asked for at most; past it a circle is asked again
#: at a quarter of its radius, so the nearest are not lost to a cut.
OUT_MAX = 1000
NARROW_TRIES = 4
#: How long an answer is kept, and how many are.
KEEP_S = 300
KEEP_MAX = 256
#: A circle's centre is rounded to this many decimals (about 110 m) for the
#: cache, and the circle asked widened by ROUND_PAD_M to cover the rounding.
ROUND = 3
ROUND_PAD_M = 120
TIMEOUT_S = 20
SOURCE = map_osm.SOURCE
#: A route searched along: at most this many points, this long, and this
#: far either side of it (500 m unless asked).
ALONG_MAX_POINTS = 200
ALONG_MAX_M = 150_000
ALONG_DEFAULT_RADIUS_M = 500
ALONG_MAX_RADIUS_M = 3000

#: Tags passed on as `tags`: these keys, and any starting with a prefix here.
TAG_KEYS = frozenset({"brand", "operator", "cuisine", "wheelchair", "fee", "access", "capacity",
                      "level", "changing_table", "bottle", "drive_through", "takeaway",
                      "outdoor_seating", "diet:vegetarian", "diet:vegan", "dispensing",
                      "emergency", "covered", "lit", "shelter", "bench", "route_ref",
                      "network", "atm", "maxstay", "parking", "amperage", "voltage",
                      "check_date:opening_hours", "description", "unisex", "male", "female"})
TAG_PREFIXES = ("socket:", "fuel:", "charging_station:")

_lock = threading.Lock()
_kept: dict[tuple, tuple[float, list[dict], float | None]] = {}


def kinds() -> list[dict]:
    """The kinds a request may name, for the app's chips: `[{id, name, aliases}]`."""
    return [{"id": k, "name": v["name"], "aliases": list(v["aliases"])} for k, v in KINDS.items()]


def _norm(word: str) -> str:
    return re.sub(r"[\s_-]+", " ", word.strip().lower())


_ALIASES: dict[str, str] = {}
for _k, _v in KINDS.items():
    for _w in (_k, _v["name"], *_v["aliases"]):
        _ALIASES[_norm(_w)] = _k


def kind_of(word: str) -> str | None:
    """The kind a word names (its id, name or an alias; a plural too), or None."""
    w = _norm(word)
    if not w:
        return None
    for cand in (w, w[:-1] if w.endswith("s") else "", w[:-2] if w.endswith("es") else ""):
        if cand and cand in _ALIASES:
            return _ALIASES[cand]
    return None


def _distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * 6371008.8 * math.asin(min(1.0, math.sqrt(a)))


def _query(kind_ids: list[str], area: str) -> str:
    parts = "".join(f"nwr{f}{area};" for k in kind_ids for f in KINDS[k]["filters"])
    return f"[out:json][timeout:{TIMEOUT_S}];({parts});out center tags {OUT_MAX};"


def _kind_for(tags: dict, kind_ids: list[str]) -> str:
    """Which of the asked kinds an element is (the first whose filter fits)."""
    for k in kind_ids:
        for f in KINDS[k]["filters"]:
            ok = True
            for key, op, val in re.findall(r'\["([^"]+)"(!?=)"([^"]*)"\]', f):
                if (tags.get(key) == val) != (op == "="):
                    ok = False
                    break
            if ok:
                return k
    return kind_ids[0]


def _address(tags: dict) -> str:
    street = " ".join(x for x in (tags.get("addr:housenumber"), tags.get("addr:street")) if x)
    town = tags.get("addr:suburb") or tags.get("addr:city") or tags.get("addr:place") or ""
    return ", ".join(x for x in (street, town) if x) or tags.get("addr:full", "")


def _place(el: dict, kind_ids: list[str]) -> dict | None:
    """A place from an Overpass element (a node's point, a way's or a
    relation's centre), its hours not yet read."""
    tags = el.get("tags") or {}
    lat, lon = el.get("lat"), el.get("lon")
    if lat is None and isinstance(el.get("center"), dict):
        lat, lon = el["center"].get("lat"), el["center"].get("lon")
    if lat is None or lon is None:
        return None
    kind = _kind_for(tags, kind_ids)
    out: dict = {"id": f"{el.get('type')}/{el.get('id')}", "kind": kind,
                 "name": tags.get("name") or tags.get("brand") or KINDS[kind]["name"],
                 "lat": round(float(lat), 7), "lon": round(float(lon), 7)}
    if addr := _address(tags):
        out["address"] = addr
    if oh := tags.get("opening_hours"):
        out["opening_hours"] = oh
    if phone := tags.get("phone") or tags.get("contact:phone"):
        out["phone"] = phone
    if web := tags.get("website") or tags.get("contact:website"):
        out["website"] = web
    out["tags"] = {k: v for k, v in tags.items() if k in TAG_KEYS or k.startswith(TAG_PREFIXES)}
    return out


def _as_of(got: dict) -> float | None:
    raw = ((got.get("osm3s") or {}).get("timestamp_osm_base") or "").replace("Z", "+00:00")
    try:
        return dt.datetime.fromisoformat(raw).timestamp() if raw else None
    except ValueError:
        return None


def open_state(hours: str, lat: float, lon: float,
               now: dt.datetime | None = None) -> tuple[bool | None, str | None]:
    """`(open_now, changes_at)` for an OSM opening_hours string at a place:
    True or False, or None when it cannot be read or says unknown; and when
    that next changes (ISO, the place's own time), or None."""
    try:
        import opening_hours as _oh
    except ImportError as e:
        _failures.swallowed(e)
        return None, None
    if now is not None and now.tzinfo is not None and not hasattr(now.tzinfo, "key"):
        # The parser takes a zoneinfo time (or a naive one): UTC's will do.
        from zoneinfo import ZoneInfo

        now = now.astimezone(ZoneInfo("UTC"))
    try:
        oh = _oh.OpeningHours(hours, coords=(lat, lon))
        state, _comment = oh.state(now)
        nxt = oh.next_change(now)
    except Exception as e:  # noqa: BLE001 - a malformed tag is the map's, not ours
        _failures.swallowed(e)
        return None, None
    name = getattr(state, "name", str(state)).upper()
    is_open = True if name == "OPEN" else False if name == "CLOSED" else None
    return is_open, nxt.isoformat() if nxt is not None else None


def _parse_near(near: str) -> tuple[float, float] | None:
    try:
        lat, lon = (float(x) for x in (near or "").split(","))
    except ValueError:
        return None
    if not (math.isfinite(lat) and math.isfinite(lon) and -90 <= lat <= 90 and -180 <= lon <= 180):
        return None
    return lat, lon


def _parse_bbox(raw: str) -> tuple[float, float, float, float] | None:
    try:
        w, s, e, n = (float(x) for x in (raw or "").split(","))
    except ValueError:
        return None
    if not all(math.isfinite(x) for x in (w, s, e, n)):
        return None
    if not (-180 <= w < e <= 180 and -90 <= s < n <= 90):
        return None
    return w, s, e, n


def _fetched(key: tuple, query: str, clock: Callable[[], float],
             fetch: Callable[[str], dict]) -> tuple[list[dict], float | None, bool]:
    """The elements for a query, kept or asked: `(elements, as_of, cached)`."""
    now = clock()
    with _lock:
        hit = _kept.get(key)
        if hit and now - hit[0] <= KEEP_S:
            return hit[1], hit[2], True
    got = fetch(query)
    elements = [e for e in got.get("elements") or [] if isinstance(e, dict)]
    as_of = _as_of(got)
    with _lock:
        _kept[key] = (now, elements, as_of)
        if len(_kept) > KEEP_MAX:
            for old in sorted(_kept, key=lambda k: _kept[k][0])[: len(_kept) - KEEP_MAX]:
                del _kept[old]
    return elements, as_of, False


def forget() -> None:
    """Drop every kept answer (tests)."""
    with _lock:
        _kept.clear()


def _parse_along(along: str) -> list[tuple[float, float]] | None:
    """`lat,lon;lat,lon;…` → the points, rounded to ROUND (the cache key and
    the query), repeats dropped; None when unreadable, too few or too many."""
    pts: list[tuple[float, float]] = []
    for part in (along or "").split(";"):
        if not part.strip():
            continue
        pt = _parse_near(part)
        if pt is None:
            return None
        r = (round(pt[0], ROUND), round(pt[1], ROUND))
        if not pts or pts[-1] != r:
            pts.append(r)
    if not 1 <= len(pts) <= ALONG_MAX_POINTS:
        return None
    return pts


def _along_line(pts: list[tuple[float, float]], lat: float, lon: float) -> tuple[float, float]:
    """How far along the line its nearest point to (lat, lon) is, and how
    far off it (lat, lon) is, in metres (flat within each segment)."""
    if len(pts) == 1:
        return 0.0, _distance_m(pts[0][0], pts[0][1], lat, lon)
    best = (math.inf, 0.0)  # (off, along)
    gone = 0.0
    for (a_lat, a_lon), (b_lat, b_lon) in zip(pts, pts[1:]):
        kx = math.cos(math.radians((a_lat + b_lat) / 2)) * 111_320.0
        ky = 110_540.0
        bx, by = (b_lon - a_lon) * kx, (b_lat - a_lat) * ky
        px, py = (lon - a_lon) * kx, (lat - a_lat) * ky
        seg2 = bx * bx + by * by
        f = 0.0 if seg2 == 0 else max(0.0, min(1.0, (px * bx + py * by) / seg2))
        off = math.hypot(px - f * bx, py - f * by)
        seg = math.sqrt(seg2)
        if off < best[0]:
            best = (off, gone + f * seg)
        gone += seg
    return best[1], best[0]


def _line_m(pts: list[tuple[float, float]]) -> float:
    return sum(_distance_m(a[0], a[1], b[0], b[1]) for a, b in zip(pts, pts[1:]))


def _bad(code: str, error: str, **more) -> tuple[bool, dict]:
    return False, {"status": 400, "code": code, "error": error, **more}


def places(near: str = "", bbox: str = "", kind: str = "", radius_m: str = "", open_now: str = "",
           limit: str = "", *, along: str = "", clock: Callable[[], float] = time.time,
           fetch: Callable[[str], dict] | None = None,
           now: dt.datetime | None = None) -> tuple[bool, dict]:
    """`GET /map/places` behind the map's gate (map_layers.places): `(ok, answer)`."""
    fetch = fetch or (lambda q: map_osm.overpass(q, timeout=TIMEOUT_S + 5))
    words = [w for w in re.split(r"[,]", kind or "") if w.strip()]
    if not words:
        return _bad("no_kind", "name a kind", kinds=list(KINDS))
    kind_ids: list[str] = []
    for w in words:
        k = kind_of(w)
        if k is None:
            return _bad("unknown_kind", f"no kind {w.strip()!r}", kinds=list(KINDS))
        if k not in kind_ids:
            kind_ids.append(k)
    try:
        lim = int(limit) if limit else DEFAULT_LIMIT
    except ValueError:
        return _bad("bad_limit", "limit is a number")
    lim = max(1, min(lim, MAX_LIMIT))
    want_open = str(open_now or "").lower() in ("1", "true", "yes")
    if sum(bool(x) for x in (near, bbox, along)) > 1:
        return _bad("near_or_bbox", "give one of near, bbox or along")
    t = clock()
    answer: dict = {"kinds": kind_ids}
    centre: tuple[float, float]
    radius: float | None = None
    line: list[tuple[float, float]] | None = None
    try:
        if near:
            pt = _parse_near(near)
            if pt is None:
                return _bad("bad_near", "near is lat,lon")
            try:
                radius = float(radius_m) if radius_m else float(DEFAULT_RADIUS_M)
            except ValueError:
                return _bad("bad_radius", "radius_m is a number of metres")
            if not math.isfinite(radius) or radius <= 0:
                return _bad("bad_radius", "radius_m is a number of metres")
            radius = min(radius, float(MAX_RADIUS_M))
            centre = pt
            clat, clon = round(pt[0], ROUND), round(pt[1], ROUND)
            ask_r = int(math.ceil(radius)) + ROUND_PAD_M
            elements, as_of, cached = [], None, False
            for tries in range(NARROW_TRIES, 0, -1):
                q = _query(kind_ids, f"(around:{ask_r},{clat},{clon})")
                elements, as_of, cached = _fetched(("near", tuple(kind_ids), clat, clon, ask_r), q, clock, fetch)
                if len(elements) < OUT_MAX:
                    break
                if tries == 1 or ask_r <= 500:
                    answer["truncated"] = True
                    break
                # Cut short at OUT_MAX in id order: ask a smaller circle so
                # the nearest are all in it.
                ask_r = max(500, ask_r // 4)
                radius = min(radius, float(ask_r - ROUND_PAD_M))
                answer["narrowed"] = True
            answer.update(near=[pt[0], pt[1]], radius_m=round(radius))
        elif along:
            line = _parse_along(along)
            if line is None:
                return _bad("bad_along", f"along is lat,lon;lat,lon;… with 1 to {ALONG_MAX_POINTS} points")
            if _line_m(line) > ALONG_MAX_M:
                return _bad("too_long", f"a route searched along is at most {ALONG_MAX_M // 1000} km")
            try:
                radius = float(radius_m) if radius_m else float(ALONG_DEFAULT_RADIUS_M)
            except ValueError:
                return _bad("bad_radius", "radius_m is a number of metres")
            if not math.isfinite(radius) or radius <= 0:
                return _bad("bad_radius", "radius_m is a number of metres")
            radius = min(radius, float(ALONG_MAX_RADIUS_M))
            ask_r = int(math.ceil(radius)) + ROUND_PAD_M
            coords = ",".join(f"{a},{b}" for a, b in line)
            q = _query(kind_ids, f"(around:{ask_r},{coords})")
            elements, as_of, cached = _fetched(("along", tuple(kind_ids), tuple(line), ask_r), q, clock, fetch)
            if len(elements) >= OUT_MAX:
                answer["truncated"] = True
            centre = line[0]
            answer.update(along_points=len(line), radius_m=round(radius))
        elif bbox:
            box = _parse_bbox(bbox)
            if box is None:
                return _bad("bad_bbox", "bbox is w,s,e,n")
            w, s, e, n = box
            if e - w > MAX_BOX_DEG or n - s > MAX_BOX_DEG:
                return _bad("too_big", f"a box's sides are at most {MAX_BOX_DEG}°")
            # Rounded outwards, so a box moved a little is the same query.
            f = 10**ROUND
            rw, rs = (math.floor(round(x * f, 6)) / f for x in (w, s))
            re_, rn = (math.ceil(round(x * f, 6)) / f for x in (e, n))
            q = _query(kind_ids, f"({rs},{rw},{rn},{re_})")
            elements, as_of, cached = _fetched(("bbox", tuple(kind_ids), rw, rs, re_, rn), q, clock, fetch)
            centre = ((s + n) / 2, (w + e) / 2)
            answer["bbox"] = [w, s, e, n]
        else:
            return _bad("no_area", "give near=lat,lon, bbox=w,s,e,n or along=lat,lon;…")
    except Exception as e:  # noqa: BLE001 - every Overpass server failed
        _failures.swallowed(e)
        return False, {"status": 502, "code": "overpass", "error": "no Overpass server answered"}
    found: list[tuple[float, dict]] = []
    seen: set[str] = set()
    for el in elements:
        p = _place(el, kind_ids)
        if p is None or p["id"] in seen:
            continue
        d = _distance_m(centre[0], centre[1], p["lat"], p["lon"])
        if line is not None:
            ahead, off = _along_line(line, p["lat"], p["lon"])
            if off > radius:
                continue
            # In the order the route reaches them; the way there is roughly the route, then off it.
            d = ahead
            p["ahead_m"], p["off_route_m"] = round(ahead), round(off)
            p["distance_m"] = round(ahead + off)
        elif radius is not None:
            if d > radius:
                continue
            p["distance_m"] = round(d)
        elif bbox:
            w, s, e, n = answer["bbox"]
            if not (s <= p["lat"] <= n and w <= p["lon"] <= e):
                continue
        seen.add(p["id"])
        found.append((d, p))
    found.sort(key=lambda dp: dp[0])
    out: list[dict] = []
    unknown = 0
    for _d, p in found:
        if "opening_hours" in p:
            p["open_now"], changes = open_state(p["opening_hours"], p["lat"], p["lon"], now)
            if changes:
                p["changes_at"] = changes
        else:
            p["open_now"] = None
        if want_open and p["open_now"] is not True:
            unknown += p["open_now"] is None
            continue
        out.append(p)
        if len(out) >= lim:
            break
    answer.update(at=round(t, 3), places=out, source=SOURCE, cached=cached)
    if as_of:
        answer["as_of"] = round(as_of)
    if want_open:
        answer["open_now"] = True
        answer["hours_unknown"] = unknown
    return True, answer
