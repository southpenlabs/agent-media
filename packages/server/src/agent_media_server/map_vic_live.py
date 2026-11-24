"""Victoria live: VicEmergency's incidents and warnings, and the Department
of Transport and Planning's road closures and roadworks, as Map layers and
in route planning (maps proposal step 9, third item; David, 24 Nov 2026:
"Go on with Victoria live").

Two layers, read for the box on screen (`GET /map/layer?id=&bbox=`, §6.50),
both only where `MEDIA_MAP_VIC=1` (a server in Victoria):

- `vic_emergency`: VicEmergency's incidents (fires, floods, rescues, planned
  burns) as points and its warnings as areas, each coloured by its warning
  level: Emergency Warning red, Watch and Act orange, Advice yellow, other
  community information blue; an incident without a warning is a smaller
  dot by kind. `MEDIA_VIC_EMERGENCY=0` turns it off.
- `vic_roads`: road closures (a full closure dark red, a lane closure
  orange) and roadworks (amber, dashed), as lines where the line is the
  closure's own and points otherwise. Only with the owner's key for
  Transport Victoria's Open Data portal (`MEDIA_VIC_ROADS_KEY`; free, the
  account is the owner's to make): without it the layer is not offered.

Each feature has `card: true` and what the app's card shows: `title`,
`badge` (the warning level or the closure's kind, in the source's words),
`at` (updated, epoch s), `where`, `detail` (the advice or description, as
plain text), `link` and `link_label` (the source's own page), `credit`.

Routing (map_route.py): a driving route avoids every full closure in force
now, each passed to Valhalla as `exclude_locations` (a few points inside
the closed stretch, so the roads crossing it stay open); `annotate()` then
gives each route `hazards: {alerts, avoided, roadworks, credit}`, where
`alerts` names each Watch and Act or Emergency Warning area the route goes
through (or within WARNING_NEAR_M of, for a warning given as a point) and
each full closure still on it; an Emergency Warning carries its `area`
(rings) so the app can say so as the car enters it.

Sources and terms (read 24 Nov 2026):

- VicEmergency (Emergency Management Victoria): the GeoJSON the public map
  at emergency.vic.gov.au loads, `public/events-geojson.json` (incidents
  and warnings; the bigger `osom-geojson.json` adds past burn areas, not
  wanted). EMV's data notice: the State of Victoria owns the copyright and
  releases it under CC BY 3.0 AU, crediting "State of Victoria" (images,
  logos and branding excluded); its support page says a formal data feed
  is by request. So one request at most every EMERGENCY_KEEP_S however
  many devices look, a named User-Agent, the credit shown, and the source
  page linked from every card. Never a substitute for VicEmergency itself:
  the card's link opens it.
- Transport Victoria Open Data (Department of Transport and Planning),
  "Unplanned Disruptions - Road" v3 (refreshed every minute) and "Planned
  Disruptions - Road" v1 (every 30 minutes), CC BY 4.0, crediting the
  Department; a free key per account, sent as the `KeyID` header (the
  gateway's own error names it, though the planned API's OpenAPI says
  Ocp-Apim-Subscription-Key, so both are sent); rate limits of 10-20 calls
  a minute, so unplanned is kept ROADS_KEEP_S and planned PLANNED_KEEP_S.
  A planned feature's LineString can be the whole highway the work is on
  (StreetWorks-SDK found one 150 km long), so a planned line is drawn only
  when short, else its point; an unplanned line longer than LONG_LINE_M
  likewise. The VicTraffic site no longer resolves (24 Nov 2026), so there
  is no keyless source of Victoria's closures; TomTom's incidents
  (map_traffic.py) already show closures and roadworks as points, so this
  layer is the official one, for drawing and for avoiding.

Open source looked at: python-aio-geojson-vicemergency-incidents (Apache-2.0)
reads the osom feed as Home Assistant entities over aiohttp, filtered by
distance from home, with no warning levels or polygons kept; StreetWorks-SDK
reads only the planned roads feed into its own works model. Neither fits a
box-on-screen layer or route checks, so the two feeds are read directly; the
StreetWorks-SDK's notes on the planned feed (the KeyID header, the long
lines, naive local times) are followed here.
"""

from __future__ import annotations

import datetime as _dt
import html
import json
import math
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from zoneinfo import ZoneInfo

from agent_media_core import failures as _failures

EMERGENCY_URL = "https://emergency.vic.gov.au/public/events-geojson.json"
EMERGENCY_PAGE = "https://emergency.vic.gov.au/respond/"
UNPLANNED_URL = "https://api.opendata.transport.vic.gov.au/api/opendata/roads/disruptions/unplanned/v3"
PLANNED_URL = "https://api.opendata.transport.vic.gov.au/opendata/roads/disruptions/planned/v1/"
ROADS_PAGE = "https://opendata.transport.vic.gov.au/dataset/unplanned-disruptions-road"
USER_AGENT = "agent-media map (https://github.com/southpenlabs/agent-media)"
EMERGENCY_CREDIT = "VicEmergency, © State of Victoria, CC BY 3.0 AU"
ROADS_CREDIT = "Department of Transport and Planning, CC BY 4.0"
TIMEOUT_S = 15
#: VicEmergency asked at most this often (its own map refreshes each minute).
EMERGENCY_KEEP_S = 90.0
#: Unplanned road disruptions (refreshed upstream each minute; 10-20 calls a minute allowed).
ROADS_KEEP_S = 120.0
#: Planned disruptions (refreshed upstream every 30 minutes).
PLANNED_KEEP_S = 30 * 60.0
#: A failed fetch is not tried again for this long; the last good copy stands meanwhile.
RETRY_S = 60.0
#: The last good copy is shown at most this long after it was fetched.
STALE_S = 30 * 60.0
#: Pages read at most per roads feed (1000 a page).
MAX_PAGES = 8
#: A source line longer than this is a corridor, not a closure's own stretch.
LONG_LINE_M = 5000.0
#: A route passing within this of a closure's line is on it.
ON_ROUTE_M = 25.0
#: A Watch and Act or Emergency Warning given as a point, not an area: this near counts.
WARNING_NEAR_M = 1000.0
#: Locations handed to Valhalla at most (its max_exclude_locations defaults to 50).
MAX_EXCLUDE = 50
#: Closures looked for this far around a trip's points.
EXCLUDE_MARGIN_KM = 15.0
#: An Emergency Warning area handed to the app with at most this many points a ring.
AREA_POINTS = 200
TZ = ZoneInfo("Australia/Melbourne")

#: Warning levels, most serious first: (code, words, colour).
LEVELS = {
    "emergency_warning": ("Emergency Warning", "#d32f2f", 4),
    "watch_and_act": ("Watch and Act", "#f57c00", 3),
    "advice": ("Advice", "#f2c200", 2),
    "community": ("Community information", "#2f7fd8", 1),
}
#: An incident without a warning: a smaller dot by kind.
INCIDENT_COLOURS = {"fire": "#b3471d", "flood": "#1f6fd1", "other": "#6d6d6d"}
ROAD_COLOURS = {"closed": "#9b111e", "lanes": "#ef6c00", "roadworks": "#f2a900"}

EMERGENCY_LAYER = {"id": "vic_emergency", "name": "Emergencies (VicEmergency)", "kind": "areas",
                   "icon": "warning", "colour": LEVELS["watch_and_act"][1], "default_on": True,
                   "bbox": True, "min_zoom": 5, "refresh_s": 120, "source": EMERGENCY_CREDIT,
                   "note": "Fires, floods and other incidents, and warnings by level. In an emergency, "
                           "VicEmergency and Triple Zero (000), not this map."}
ROADS_LAYER = {"id": "vic_roads", "name": "Road closures & roadworks", "kind": "lines",
               "icon": "roadworks", "colour": ROAD_COLOURS["closed"], "default_on": False,
               "bbox": True, "min_zoom": 8, "refresh_s": 300, "source": ROADS_CREDIT,
               "note": "Closures and roadworks on Victoria's roads, as the Department of Transport and Planning has them."}

Fetch = Callable[[str, dict], bytes]


def _on(name: str, default: bool) -> bool:
    raw = (os.environ.get(name) or "").strip().lower()
    return default if not raw else raw in ("1", "on", "yes", "true")


def vic() -> bool:
    return _on("MEDIA_MAP_VIC", False)


def emergency_on() -> bool:
    return vic() and _on("MEDIA_VIC_EMERGENCY", True)


def roads_key() -> str:
    return (os.environ.get("MEDIA_VIC_ROADS_KEY") or "").strip()


def roads_on() -> bool:
    return vic() and bool(roads_key())


def layers() -> list[dict]:
    out = []
    if emergency_on():
        out.append(dict(EMERGENCY_LAYER))
    if roads_on():
        out.append(dict(ROADS_LAYER))
    return out


def _get(url: str, headers: dict) -> bytes:
    """GET `url` (no redirects followed elsewhere: the key goes to one host only)."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json", **headers})
    with urllib.request.urlopen(req, timeout=TIMEOUT_S) as res:
        if urllib.parse.urlsplit(res.geturl()).netloc != urllib.parse.urlsplit(url).netloc:
            raise urllib.error.URLError("redirected to another host")
        return res.read()


# --- keeping ------------------------------------------------------------------

class _Kept:
    """One feed's last good answer, fetched at most every `keep_s`, one fetch at a time."""

    def __init__(self, keep_s: float) -> None:
        self.keep_s = keep_s
        self.lock = threading.Lock()
        self.at: float | None = None
        self.tried: float | None = None
        self.value: list[dict] | None = None

    def get(self, load: Callable[[], list[dict]], now: float) -> tuple[list[dict] | None, bool]:
        """(features or None, stale)."""
        with self.lock:
            fresh = self.at is not None and now - self.at < self.keep_s
            backing_off = self.tried is not None and now - self.tried < RETRY_S
            if not fresh and not backing_off:
                self.tried = now
                try:
                    self.value = load()
                    self.at = now
                except Exception as e:  # noqa: BLE001 - the last good copy stands
                    _failures.swallowed(e)
            if self.value is None or self.at is None or now - self.at > STALE_S:
                return None, False
            return self.value, now - self.at >= self.keep_s


_emergency = _Kept(EMERGENCY_KEEP_S)
_unplanned = _Kept(ROADS_KEEP_S)
_planned = _Kept(PLANNED_KEEP_S)


def _reset_for_tests() -> None:
    global _emergency, _unplanned, _planned
    _emergency, _unplanned, _planned = _Kept(EMERGENCY_KEEP_S), _Kept(ROADS_KEEP_S), _Kept(PLANNED_KEEP_S)


# --- geometry -----------------------------------------------------------------

def _metres(a: list[float], b: list[float]) -> float:
    kx = math.cos(math.radians((a[1] + b[1]) / 2)) * 111_320.0
    return math.hypot((a[0] - b[0]) * kx, (a[1] - b[1]) * 110_540.0)


def _length(line: list[list[float]]) -> float:
    return sum(_metres(line[i], line[i + 1]) for i in range(len(line) - 1))


def _to_seg(p: list[float], a: list[float], b: list[float]) -> float:
    """Metres from p to the segment a-b (flat, fine at these sizes)."""
    kx = math.cos(math.radians(p[1])) * 111_320.0
    ky = 110_540.0
    ax, ay = (a[0] - p[0]) * kx, (a[1] - p[1]) * ky
    bx, by = (b[0] - p[0]) * kx, (b[1] - p[1]) * ky
    dx, dy = bx - ax, by - ay
    d2 = dx * dx + dy * dy
    t = 0.0 if d2 == 0 else max(0.0, min(1.0, -(ax * dx + ay * dy) / d2))
    return math.hypot(ax + t * dx, ay + t * dy)


def _in_ring(p: list[float], ring: list[list[float]]) -> bool:
    x, y = p[0], p[1]
    inside = False
    j = len(ring) - 1
    for i in range(len(ring)):
        xi, yi = ring[i][0], ring[i][1]
        xj, yj = ring[j][0], ring[j][1]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / ((yj - yi) or 1e-12) + xi:
            inside = not inside
        j = i
    return inside


def _rings(geom: dict) -> list[list[list[float]]]:
    """The outer rings of a Polygon or MultiPolygon."""
    if geom.get("type") == "Polygon":
        return [geom["coordinates"][0]] if geom.get("coordinates") else []
    if geom.get("type") == "MultiPolygon":
        return [p[0] for p in geom.get("coordinates") or [] if p]
    return []


def _all_points(geom: dict) -> list[list[float]]:
    t, c = geom.get("type"), geom.get("coordinates") or []
    if t == "Point":
        return [c]
    if t in ("LineString", "MultiPoint"):
        return list(c)
    if t in ("MultiLineString", "Polygon"):
        return [p for part in c for p in part]
    if t == "MultiPolygon":
        return [p for poly in c for ring in poly for p in ring]
    return []


def _bounds(geom: dict) -> tuple[float, float, float, float] | None:
    pts = _all_points(geom)
    if not pts:
        return None
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    return (min(xs), min(ys), max(xs), max(ys))


def _meets(b: tuple[float, float, float, float] | None, box) -> bool:
    if b is None:
        return False
    if box is None:
        return True
    w, s, e, n = box
    return not (b[2] < w or b[0] > e or b[3] < s or b[1] > n)


def _thin(ring: list[list[float]], most: int) -> list[list[float]]:
    if len(ring) <= most:
        return ring
    step = len(ring) / most
    out = [ring[int(i * step)] for i in range(most)]
    return out + [ring[-1]]


def _valid_point(c) -> bool:
    return (isinstance(c, (list, tuple)) and len(c) >= 2 and all(isinstance(v, (int, float)) for v in c[:2])
            and -180 <= c[0] <= 180 and -90 <= c[1] <= 90)


# --- VicEmergency -------------------------------------------------------------

def _text(raw) -> str:
    """Plain words from the feed's text or HTML, blank lines kept as one."""
    s = "" if raw in (None, "None") else str(raw)
    s = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</li>", "\n", s)
    s = html.unescape(re.sub(r"<[^>]+>", " ", s))
    lines = [re.sub(r"\s+([.,;:!?])", r"\1", " ".join(x.split())) for x in s.splitlines()]
    out: list[str] = []
    for ln in lines:
        if ln or (out and out[-1]):
            out.append(ln)
    return "\n".join(out).strip()


def level_of(p: dict) -> str | None:
    """A warning's level code, or None for an incident without one."""
    if (p.get("feedType") or "").lower() != "warning":
        return None
    words = " ".join(str(p.get(k) or "") for k in ("category1", "name", "sourceTitle")).lower()
    if "emergency warning" in words or "evacuat" in words:
        return "emergency_warning"
    if "watch and act" in words or "watch & act" in words:
        return "watch_and_act"
    if "advice" in words:
        return "advice"
    return "community"


def _when(raw) -> float | None:
    if not raw or raw == "None":
        return None
    try:
        d = _dt.datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=TZ)
    return d.timestamp()


def _incident_kind(p: dict) -> str:
    words = f"{p.get('category1') or ''} {p.get('category2') or ''}".lower()
    if "fire" in words or "burn" in words:
        return "fire"
    if "flood" in words:
        return "flood"
    return "other"


def emergency_features(raw: dict) -> list[dict]:
    """The feed's features as the layer's: a warning's areas and point apart,
    each with the card's properties; incidents as points."""
    out: list[dict] = []
    for f in (raw or {}).get("features") or []:
        p = f.get("properties") or {}
        geom = f.get("geometry") or {}
        level = level_of(p)
        kind = _incident_kind(p)
        title = " ".join(str(p.get("sourceTitle") or "").split())
        cat = p.get("category2") if p.get("category2") not in (None, "", "Other") else p.get("category1")
        if level:
            event = ""
            cap = p.get("cap")
            if isinstance(cap, dict):
                event = cap.get("event") or ""
            title = " - ".join(x for x in (LEVELS[level][0], event or cat, p.get("location")) if x)
            badge = LEVELS[level][0]
            colour = LEVELS[level][1]
            link = f"{EMERGENCY_PAGE}#!/warning/{p.get('sourceId') or p.get('id')}/moreinfo"
            text = _text(p.get("webBody")) or _text(p.get("text"))
            action = _s(p.get("action"))
            detail = "\n\n".join(x for x in (action, text[:1500]) if x)
        else:
            if not title or title.lower() in ("undefined", "none"):
                title = str(cat or p.get("category1") or "Incident")
            elif cat and str(cat).lower() not in title.lower():
                title = f"{cat}: {title}"
            badge = str(p.get("status") or "") or str(p.get("category1") or "")
            colour = INCIDENT_COLOURS[kind]
            link = str(p.get("url")) if str(p.get("url") or "").startswith("https://") else EMERGENCY_PAGE
            bits = [f"{p.get('category1')}" if p.get("category1") else "",
                    f"Status: {p['status']}" if p.get("status") else "",
                    f"Size: {p['sizeFmt']}" if p.get("sizeFmt") not in (None, "", "None") else "",
                    f"Agency: {p['sourceOrg']}" if p.get("sourceOrg") else ""]
            detail = " · ".join(b for b in bits if b)
        props = {"card": True, "title": title, "badge": badge, "colour": colour,
                 "where": " ".join(str(p.get("location") or "").split()), "detail": detail,
                 "link": link, "link_label": "VicEmergency", "credit": EMERGENCY_CREDIT,
                 "source_id": str(p.get("id") or ""), "category": str(p.get("category1") or ""),
                 "kind": "warning" if level else "incident", "incident": kind}
        at = _when(p.get("updated")) or _when(p.get("created"))
        if at:
            props["at"] = round(at)
        if level:
            props["level"] = level
            props["rank"] = LEVELS[level][2]
        parts = geom.get("geometries") if geom.get("type") == "GeometryCollection" else [geom]
        for g in parts or []:
            t = (g or {}).get("type")
            if t == "Point" and _valid_point(g.get("coordinates")):
                out.append({"type": "Feature", "geometry": g, "properties": dict(props)})
            elif t in ("Polygon", "MultiPolygon") and g.get("coordinates"):
                out.append({"type": "Feature", "geometry": g, "properties": {**props, "area": True}})
    return out


def _load_emergency(fetch: Fetch | None) -> Callable[[], list[dict]]:
    def load() -> list[dict]:
        url = os.environ.get("MEDIA_VIC_EMERGENCY_URL") or EMERGENCY_URL
        body = (fetch or _get)(url, {})
        return emergency_features(json.loads(body.decode("utf-8") or "{}"))
    return load


def emergencies(fetch: Fetch | None = None, now: float | None = None) -> tuple[list[dict] | None, bool]:
    if not emergency_on():
        return [], False
    return _emergency.get(_load_emergency(fetch), time.time() if now is None else now)


def read_emergency(bbox, fetch: Fetch | None = None, now: float | None = None) -> tuple[list[dict], dict]:
    feats, stale = emergencies(fetch, now)
    if feats is None:
        return [], {"unreachable": True}
    got = [f for f in feats if _meets(_bounds(f["geometry"]), bbox)]
    # The most serious drawn last (on top); areas under points.
    got.sort(key=lambda f: (0 if f["properties"].get("area") else 1, f["properties"].get("rank", 0)))
    return got, ({"stale": True} if stale else {})


# --- the roads ----------------------------------------------------------------

_INACTIVE = re.compile(r"^(inactive|reopened|closed|completed?|cancell?ed|pending|finished|expired)$", re.I)
_FULL = re.compile(r"road closed|closed to all|all lanes closed|full(y)? clos|^closed$|^closure$|road closure", re.I)
_LANES = re.compile(r"lane|partial|reduced|shoulder|contraflow|one way|single|narrow", re.I)
_WORKS = re.compile(r"works|maintenance|construction|resurfac|repair|upgrade|install", re.I)


def _d(v) -> dict:
    return v if isinstance(v, dict) else {}


def _s(v) -> str:
    return "" if v in (None, "None") else " ".join(str(v).split())


def road_kind(p: dict, planned: bool) -> str:
    """closed (the road shut), lanes (part of it) or roadworks (works, open)."""
    impact = _d(p.get("impact"))
    access = " ".join(x for x in (_s(impact.get("impactType")), _s(p.get("roadAccessType"))) if x)
    if access and _FULL.search(access) and not _LANES.search(access):
        return "closed"
    if access and (_LANES.search(access) or "closed" in access.lower()):
        return "lanes"
    what = " ".join(_s(p.get(k)) for k in ("eventType", "eventSubType", "eventSubtype", "eventDueTo"))
    if planned or _WORKS.search(what):
        return "roadworks"
    return "lanes" if access else "roadworks"


def _naive_local(raw) -> _dt.datetime | None:
    if not raw or raw == "None":
        return None
    try:
        d = _dt.datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None
    return d.replace(tzinfo=TZ) if d.tzinfo is None else d


_DUR = re.compile(r"^P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?)?$")
_DAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]


def in_force(p: dict, now: float) -> bool | None:
    """Whether a planned disruption is on now: True, False, or None when its
    times could not be read (shown, never avoided)."""
    d = _d(p.get("duration"))
    start, end = _naive_local(d.get("start")), _naive_local(d.get("end"))
    t = _dt.datetime.fromtimestamp(now, TZ)
    if (start and t < start) or (end and t > end):
        return False
    rec = d.get("recurrences") or []
    if not rec:
        return True if (start or end) else None
    known = False
    for r in rec:
        if not isinstance(r, dict):
            continue
        if r.get("allDay") is True and str(r.get("startDay") or "").lower() in ("", "daily", "everyday", "every day"):
            return True
        m = _DUR.match(str(r.get("duration") or ""))
        hm = re.match(r"^(\d{1,2}):(\d{2})", str(r.get("startTime") or ""))
        day = str(r.get("startDay") or "").lower()
        if not m or not hm:
            continue
        known = True
        span = _dt.timedelta(days=int(m.group(1) or 0), hours=int(m.group(2) or 0), minutes=int(m.group(3) or 0))
        if r.get("allDay") is True:
            span = _dt.timedelta(days=max(1, int(r.get("daysDuration") or 1)))
        for back in range(0, 8):
            day0 = (t - _dt.timedelta(days=back)).date()
            if day in _DAYS and _DAYS[day0.weekday()] != day:
                continue
            begin = _dt.datetime(day0.year, day0.month, day0.day, int(hm.group(1)), int(hm.group(2)), tzinfo=TZ)
            if begin <= t < begin + span:
                return True
    return False if known else None


def _road_geoms(geom: dict, planned: bool) -> list[dict]:
    """The geometries to draw: a short line as itself, a long one (a corridor) by its point."""
    parts = geom.get("geometries") if geom.get("type") == "GeometryCollection" else [geom]
    pts = [g for g in parts or [] if (g or {}).get("type") == "Point" and _valid_point(g.get("coordinates"))]
    lines = [g for g in parts or [] if (g or {}).get("type") == "LineString"
             and len(g.get("coordinates") or []) >= 2 and all(_valid_point(c) for c in g["coordinates"])]
    short = [g for g in lines if _length(g["coordinates"]) <= LONG_LINE_M]
    if short and not (planned and pts):
        return short
    if pts:
        return pts[:1]
    if lines:
        c = lines[0]["coordinates"]
        return [{"type": "Point", "coordinates": c[len(c) // 2]}]
    return []


def road_features(rows: list[dict], planned: bool, now: float) -> list[dict]:
    out: list[dict] = []
    for f in rows:
        p = f.get("properties") or {}
        src = _d(p.get("source"))
        if any(_INACTIVE.match(_s(p.get(k))) for k in ("status", "eventLocationStatus")):
            continue
        if re.search(r"tow\s*allocation", _s(src.get("sourceName")), re.I):
            continue
        on: bool | None = True
        if planned:
            on = in_force(p, now)
            if on is False:
                continue
        kind = road_kind(p, planned)
        ref = _d(p.get("reference"))
        impact = _d(p.get("impact"))
        road = (_s(p.get("closedRoadName")) or _s(p.get("declaredRoadName")) or _s(ref.get("localRoadName"))
                or "Road").title()
        frm = _s(p.get("startIntersectionRoadName") or ref.get("startIntersectionRoadName")).title()
        to = _s(p.get("endIntersectionRoadName") or ref.get("endIntersectionRoadName")).title()
        town = _s(p.get("startIntersectionLocality") or ref.get("startIntersectionLocality")).title()
        where = ", ".join(x for x in (" to ".join(x for x in (frm, to) if x) if frm else "", town) if x)
        badge = {"closed": "Road closed", "lanes": _s(impact.get("impactType")) or "Lanes closed",
                 "roadworks": "Roadworks"}[kind]
        bits = [_s(p.get("description")),
                _s(impact.get("direction")) and f"Direction: {_s(impact.get('direction'))}",
                _s(impact.get("delay")) and f"Delay: {_s(impact.get('delay'))}",
                _s(impact.get("speedLimitOnSite")) and f"Speed limit: {_s(impact.get('speedLimitOnSite'))} km/h"]
        d = _d(p.get("duration"))
        until = _naive_local(d.get("end")) if planned else _naive_local(p.get("endTime"))
        if until and planned:
            bits.append(f"Until {until.strftime('%-d %b %Y, %-I:%M %p')}")
        if on is None:
            bits.append("Times vary: check before you go.")
        props = {"card": True, "title": f"{badge}: {road}" if kind != "roadworks" else f"Roadworks: {road}",
                 "badge": badge, "where": where, "detail": "\n".join(b for b in bits if b),
                 "colour": ROAD_COLOURS[kind], "road": kind, "planned": planned,
                 "link": _s(p.get("weblinkURL")) if _s(p.get("weblinkURL")).startswith("https://") else ROADS_PAGE,
                 "link_label": "Department of Transport and Planning", "credit": ROADS_CREDIT,
                 "source_id": _s(p.get("id") or p.get("eventId"))}
        if kind == "roadworks":
            props["dashed"] = True
        if kind == "closed" and on is True:
            props["avoid"] = True
        at = _when(p.get("lastUpdated")) or _when(p.get("created"))
        if at:
            props["at"] = round(at)
        for g in _road_geoms(f.get("geometry") or {}, planned):
            out.append({"type": "Feature", "geometry": g, "properties": dict(props)})
    return out


def _pages(url: str, planned: bool, fetch: Fetch) -> list[dict]:
    key = roads_key()
    headers = {"KeyID": key, "Ocp-Apim-Subscription-Key": key}
    rows: list[dict] = []
    token = ""
    for page in range(1, MAX_PAGES + 1):
        if planned:
            q = url + "?" + urllib.parse.urlencode({"format": "GeoJson"})
            got = json.loads(fetch(q, {**headers, **({"NextPageToken": token} if token else {})}) or b"{}")
            rows += got.get("features") or []
            nxt = got.get("nextPageDetails") or {}
            if not nxt.get("hasMoreRecords") or not nxt.get("nextPageToken") or nxt["nextPageToken"] == token:
                break
            token = nxt["nextPageToken"]
        else:
            q = url + "?" + urllib.parse.urlencode({"page": page, "limit": 1000})
            got = json.loads(fetch(q, headers) or b"{}")
            data = got.get("data") if isinstance(got.get("data"), dict) else got
            rows += data.get("features") or []
            meta = got.get("meta") or {}
            if not meta.get("total_pages") or page >= int(meta["total_pages"]):
                break
    return rows


def roads(fetch: Fetch | None = None, now: float | None = None) -> tuple[list[dict] | None, bool]:
    if not roads_on():
        return [], False
    t = time.time() if now is None else now
    f = fetch or _get
    un, s1 = _unplanned.get(lambda: road_features(_pages(os.environ.get("MEDIA_VIC_UNPLANNED_URL") or UNPLANNED_URL,
                                                         False, f), False, t), t)
    pl, s2 = _planned.get(lambda: _pages(os.environ.get("MEDIA_VIC_PLANNED_URL") or PLANNED_URL, True, f), t)
    if un is None and pl is None:
        return None, False
    # Planned rows are kept raw, and whether each is on is decided now.
    return (un or []) + road_features(pl or [], True, t), s1 or s2


def read_roads(bbox, fetch: Fetch | None = None, now: float | None = None) -> tuple[list[dict], dict]:
    feats, stale = roads(fetch, now)
    if feats is None:
        return [], {"unreachable": True}
    order = {"roadworks": 0, "lanes": 1, "closed": 2}
    got = [f for f in feats if _meets(_bounds(f["geometry"]), bbox)]
    got.sort(key=lambda f: order.get(f["properties"].get("road"), 0))
    return got, ({"stale": True} if stale else {})


# --- routing ------------------------------------------------------------------

def _inner_points(line: list[list[float]], most: int = 4) -> list[list[float]]:
    """A few points inside a closed stretch, away from its ends (where the crossing roads are)."""
    total = _length(line)
    if total < 1:
        return [line[0]]
    want = [total * (i + 1) / (most + 1) for i in range(most)] if total > 60 else [total / 2]
    out, run, i = [], 0.0, 0
    for target in want:
        while i < len(line) - 1 and run + _metres(line[i], line[i + 1]) < target:
            run += _metres(line[i], line[i + 1])
            i += 1
        if i >= len(line) - 1:
            break
        seg = _metres(line[i], line[i + 1]) or 1.0
        k = (target - run) / seg
        out.append([line[i][0] + (line[i + 1][0] - line[i][0]) * k, line[i][1] + (line[i + 1][1] - line[i][1]) * k])
    return out


def _around(points: list[tuple[float, float]], margin_km: float) -> tuple[float, float, float, float]:
    lats, lons = [p[0] for p in points], [p[1] for p in points]
    dlat = margin_km / 111.0
    dlon = margin_km / (111.0 * max(math.cos(math.radians(sum(lats) / len(lats))), 0.05))
    return (min(lons) - dlon, min(lats) - dlat, max(lons) + dlon, max(lats) + dlat)


def closures_near(points: list[tuple[float, float]], fetch: Fetch | None = None,
                  now: float | None = None) -> list[dict]:
    """The full closures in force around a trip's points (lat, lon), nearest the trip first."""
    if not roads_on() or not points:
        return []
    feats, _stale = roads(fetch, now)
    box = _around(points, EXCLUDE_MARGIN_KM)
    return [f for f in feats or [] if f["properties"].get("avoid") and _meets(_bounds(f["geometry"]), box)]


def exclusions(points: list[tuple[float, float]], fetch: Fetch | None = None,
               now: float | None = None) -> tuple[list[dict], int]:
    """Valhalla `exclude_locations` for the full closures around a trip, and
    how many closures they stand for. A closure at a trip's own end is left
    out (excluding it would leave no route)."""
    locs: list[dict] = []
    n = 0
    for f in closures_near(points, fetch, now):
        g = f["geometry"]
        pts = _inner_points(g["coordinates"]) if g["type"] == "LineString" else [g["coordinates"]]
        if any(_metres([lon, lat], q) < 150 for lat, lon in (points[0], points[-1]) for q in pts):
            continue
        if len(locs) + len(pts) > MAX_EXCLUDE:
            break
        locs += [{"lat": round(q[1], 6), "lon": round(q[0], 6)} for q in pts]
        n += 1
    return locs, n


def _dense(line: list[list[float]], step_m: float = 150.0) -> list[list[float]]:
    out: list[list[float]] = []
    for i in range(len(line) - 1):
        a, b = line[i], line[i + 1]
        out.append(a)
        d = _metres(a, b)
        for k in range(1, int(d // step_m) + 1):
            f = k * step_m / d
            if f < 1:
                out.append([a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f])
    if line:
        out.append(line[-1])
    return out


def _near_line(p: list[float], line: list[list[float]], within_m: float) -> bool:
    pad = within_m / 100_000.0
    for i in range(len(line) - 1):
        a, b = line[i], line[i + 1]
        if (min(a[0], b[0]) - pad <= p[0] <= max(a[0], b[0]) + pad
                and min(a[1], b[1]) - pad <= p[1] <= max(a[1], b[1]) + pad
                and _to_seg(p, a, b) <= within_m):
            return True
    return False


def hazards_on(line: list[list[float]], mode: str, fetch: Fetch | None = None,
               now: float | None = None, avoided: int = 0) -> dict | None:
    """What a route's line meets: Watch and Act or Emergency Warning areas
    (and such warnings given as points, within WARNING_NEAR_M), full closures
    still on it (driving and cycling), and how many roadworks."""
    if not vic() or len(line) < 2:
        return None
    lb = _bounds({"type": "LineString", "coordinates": line})
    alerts: list[dict] = []
    seen: set[str] = set()
    feats, _s1 = emergencies(fetch, now)
    dense = None
    for f in sorted(feats or [], key=lambda f: -f["properties"].get("rank", 0)):
        p = f["properties"]
        if p.get("rank", 0) < LEVELS["watch_and_act"][2] or p["source_id"] in seen:
            continue
        g = f["geometry"]
        b = _bounds(g)
        hit = False
        if p.get("area"):
            if not _meets(b, lb):
                continue
            dense = dense or _dense(line)
            rings = _rings(g)
            hit = any(_in_ring(q, r) for q in dense for r in rings
                      if b and b[0] <= q[0] <= b[2] and b[1] <= q[1] <= b[3])
        elif g["type"] == "Point":
            hit = _near_line(g["coordinates"], line, WARNING_NEAR_M)
        if not hit:
            continue
        seen.add(p["source_id"])
        a = {"kind": "emergency", "level": p["level"], "level_name": p["badge"], "title": p["title"],
             "where": p.get("where", ""), "link": p["link"], "at": p.get("at")}
        if p["level"] == "emergency_warning":
            # Every area of that warning, so the app can say so on the way in.
            a["area"] = [_thin(r, AREA_POINTS) for x in feats or [] if x["properties"]["source_id"] == p["source_id"]
                         for r in _rings(x["geometry"])]
        alerts.append(a)
    works = 0
    if mode in ("drive", "bike") and roads_on():
        rfeats, _s2 = roads(fetch, now)
        for f in rfeats or []:
            p = f["properties"]
            if p.get("road") not in ("closed", "roadworks") or not _meets(_bounds(f["geometry"]), lb):
                continue
            g = f["geometry"]
            pts = _inner_points(g["coordinates"]) if g["type"] == "LineString" else [g["coordinates"]]
            if not any(_near_line(q, line, ON_ROUTE_M) for q in pts):
                continue
            if p["road"] == "roadworks":
                works += 1
            elif p["source_id"] not in seen:
                seen.add(p["source_id"])
                alerts.append({"kind": "closure", "level": "closure", "level_name": p["badge"],
                               "title": p["title"], "where": p.get("where", ""), "link": p["link"], "at": p.get("at")})
    if not alerts and not works and not avoided:
        return None
    credit = [EMERGENCY_CREDIT] + ([ROADS_CREDIT] if roads_on() else [])
    return {"alerts": alerts, "avoided": avoided, "roadworks": works, "credit": "; ".join(credit)}


def annotate(routes: list[dict], fetch: Fetch | None = None, now: float | None = None, avoided: int = 0) -> None:
    """Each route its `hazards` (see hazards_on), where it has any."""
    for r in routes:
        try:
            h = hazards_on(r.get("line") or [], r.get("mode") or "drive", fetch, now,
                           avoided if r.get("mode") == "drive" else 0)
        except Exception as e:  # noqa: BLE001 - a route stands without its warnings
            _failures.swallowed(e)
            h = None
        if h:
            r["hazards"] = h
