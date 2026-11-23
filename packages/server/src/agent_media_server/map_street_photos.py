"""Street-level photos near a point, for the Map's place card and along a
route: a free Street View from Panoramax (open, no key) and Mapillary
(its Graph API, only with the owner's own client token). Maps proposal
step 9; David, 24 Nov 2026: "Go on with street photos".

  GET /map/street_photos?near=lat,lon[&radius_m=80][&heading=0-359][&limit=6]
      → {photos: [{source: panoramax|mapillary, id, thumb, image, link,
         date?, author?, license, license_url?, credit, heading?, pano,
         facing, distance_m, lat, lon}], sources: {panoramax: ok|failed,
         mapillary: ok|failed|off}}
  GET /map/street_photos/along?points=lat,lon[,heading];…[&radius_m=40]
      → {points: [photo | null]}: the best photo at each of at most
         ALONG_MAX points (a route's turns), Panoramax only.

Order: photos that look the right way first (along `heading` when given,
else toward the point: a card's place), then the rest; nearest first in
each. A 360° photo looks every way.

Open source first (24 Nov 2026): Panoramax is a STAC API, so
pystac-client (Apache-2.0) could ask it, but it brings pystac and its
item model for one GET whose answer is sorted here anyway; Mapillary's
own Python SDK (mapillary-python-sdk, MIT) has had no release since
2022 and wraps the same one call. Both are plain JSON over HTTPS, asked
directly. Viewing is left to each source's own viewer (Panoramax's
web viewer and MapillaryJS, both MIT) through a link; the app shows the
photo itself.

Terms (read 24 Nov 2026):
- Panoramax: each picture carries its own licence (`license`, an SPDX id:
  CC-BY-SA-4.0 on most instances, etalab-2.0 on IGN's); its author is the
  account that sent it (its last `providers` entry). Shown under the
  photo with the licence and "Panoramax". No key and no published rate
  limit: requests go one at a time with a named User-Agent, and answers
  are kept.
- Mapillary (https://www.mapillary.com/terms, effective 15 Feb 2024):
  images are CC BY-SA 4.0 by default (some sets CC BY-NC-SA); an app
  needs its own registered client; §5 forbids use "for or in connection
  with real-time navigation or route guidance", so the route's photos
  (`along`) ask Panoramax only; §11 asks for Mapillary's logo and a link
  back where its images are shown; §12 limits commercial use. Off unless
  MEDIA_MAPILLARY_TOKEN is set (the owner's own client token).

Answers are kept on disk (one file a key) in
~/.cache/agent-media/street-photos/: Panoramax's for CACHE_S, Mapillary's
for MAPILLARY_CACHE_S (its thumbnail links are signed and expire), an
empty answer for MISS_S; a failure is not kept.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from agent_media_core import failures as _failures

USER_AGENT = "agent-media/1.0 (Sasonica map street photos; +https://sasonica.com; https://github.com/southpenlabs/agent-media)"
PANORAMAX = "https://api.panoramax.xyz"
MAPILLARY = "https://graph.mapillary.com/images"
TIMEOUT_S = 8
CACHE_S = 7 * 86400
MAPILLARY_CACHE_S = 12 * 3600
MISS_S = 2 * 86400
RADIUS_M = 80
RADIUS_MAX_M = 500
ALONG_RADIUS_M = 40
ALONG_MAX = 8
LIMIT = 6
LIMIT_MAX = 12
#: How many each source is asked for in the box (sorted here).
ASK_N = 100
#: Within this many degrees of the wanted direction, a photo "faces" it.
FACING_DEG = 60
LICENSES = {
    "CC-BY-SA-4.0": ("CC BY-SA 4.0", "https://creativecommons.org/licenses/by-sa/4.0/"),
    "CC-BY-4.0": ("CC BY 4.0", "https://creativecommons.org/licenses/by/4.0/"),
    "CC0-1.0": ("CC0", "https://creativecommons.org/publicdomain/zero/1.0/"),
    "etalab-2.0": ("Licence Ouverte 2.0", "https://www.etalab.gouv.fr/licence-ouverte-open-licence/"),
}

#: (url, headers) → (status, body). Tests pass their own.
Get = Callable[[str, dict], "tuple[int, bytes]"]

_lock = threading.Lock()
_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def _get(url: str, headers: dict) -> tuple[int, bytes]:
    req = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": USER_AGENT, **headers})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:
            return r.status, r.read(8_000_000)
    except urllib.error.HTTPError as e:
        return e.code, e.read(10_000)


class _Failed(Exception):
    """A source did not answer: nothing is kept."""


def _json(url: str, get: Get | None, headers: dict | None = None) -> Any:
    with _lock:
        status, body = (get or _get)(url, headers or {})
    if status != 200:
        raise _Failed(f"{status} from {url.split('?')[0]}")
    try:
        return json.loads(body)
    except ValueError as e:
        raise _Failed(str(e)) from e


def mapillary_token() -> str:
    return (os.environ.get("MEDIA_MAPILLARY_TOKEN") or "").strip()


# --- the disk cache ----------------------------------------------------------

def _cache_root() -> Path:
    from agent_media_core._paths import cache_dir

    return cache_dir() / "street-photos"


def _file(key: str) -> Path:
    return _cache_root() / (hashlib.sha1(key.encode()).hexdigest()[:20] + ".json")


def _memo(key: str, keep_s: float, now_s: float, make: Callable[[], list]) -> list:
    p = _file(key)
    try:
        d = json.loads(p.read_text())
        if isinstance(d, dict) and d.get("key") == key:
            value = d.get("value") or []
            if now_s - float(d.get("at") or 0) < (keep_s if value else MISS_S):
                return value
    except (OSError, ValueError):
        pass
    value = make()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_name(f".{p.name}.{os.getpid()}.{threading.get_ident()}")
        tmp.write_text(json.dumps({"key": key, "at": now_s, "value": value}))
        tmp.replace(p)
    except OSError as e:
        _failures.swallowed(e)
    return value


def forget() -> None:
    """Clears the disk cache (tests; `rm -r ~/.cache/agent-media/street-photos` does the same)."""
    root = _cache_root()
    if root.is_dir():
        for f in root.glob("*.json"):
            f.unlink(missing_ok=True)


# --- geometry ----------------------------------------------------------------

def _distance_m(a: tuple[float, float], b: tuple[float, float]) -> float:
    la1, lo1, la2, lo2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 2 * 6_371_000 * math.asin(min(1.0, math.sqrt(h)))


def _bearing(a: tuple[float, float], b: tuple[float, float]) -> float:
    la1, lo1, la2, lo2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    y = math.sin(lo2 - lo1) * math.cos(la2)
    x = math.cos(la1) * math.sin(la2) - math.sin(la1) * math.cos(la2) * math.cos(lo2 - lo1)
    return (math.degrees(math.atan2(y, x)) + 360) % 360


def _off(a: float, b: float) -> float:
    return abs((a - b + 180) % 360 - 180)


def _bbox(pt: tuple[float, float], radius_m: float) -> tuple[float, float, float, float]:
    dlat = radius_m / 111_320
    dlon = radius_m / (111_320 * max(0.01, math.cos(math.radians(pt[0]))))
    return (round(pt[1] - dlon, 6), round(pt[0] - dlat, 6), round(pt[1] + dlon, 6), round(pt[0] + dlat, 6))


def _num(v: Any) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _https(u: Any) -> str:
    return u if isinstance(u, str) and u.startswith("https://") else ""


# --- Panoramax ---------------------------------------------------------------

def _panoramax(pt: tuple[float, float], radius_m: int, get: Get | None, now_s: float) -> list[dict]:
    box = _bbox(pt, radius_m)

    def make() -> list[dict]:
        q = urllib.parse.urlencode({"bbox": ",".join(str(x) for x in box), "limit": ASK_N})
        d = _json(f"{PANORAMAX}/api/search?{q}", get)
        return [p for f in (d or {}).get("features") or [] if (p := _from_panoramax(f))]

    return _memo(f"pnx|{box}", CACHE_S, now_s, make)


def _from_panoramax(f: dict) -> dict | None:
    pid = str(f.get("id") or "")
    props = f.get("properties") or {}
    coords = (f.get("geometry") or {}).get("coordinates") or []
    assets = f.get("assets") or {}
    thumb = _https((assets.get("thumb") or {}).get("href"))
    image = _https((assets.get("sd") or {}).get("href")) or thumb
    if not _ID.match(pid) or len(coords) < 2 or not thumb or props.get("geovisio:status", "ready") != "ready":
        return None
    lon, lat = _num(coords[0]), _num(coords[1])
    if lat is None or lon is None:
        return None
    spdx = str(props.get("license") or "")
    name, url = LICENSES.get(spdx, (spdx.replace("-", " ") or "see source", ""))
    lic_link = next((lk.get("href") for lk in f.get("links") or [] if lk.get("rel") == "license"), "")
    people = [p.get("name") for p in f.get("providers") or [] if isinstance(p, dict) and p.get("name")]
    fov = _num((props.get("pers:interior_orientation") or {}).get("field_of_view"))
    out: dict = {"source": "panoramax", "id": pid, "thumb": thumb, "image": image,
                 "link": f"{PANORAMAX}/#focus=pic&pic={pid}", "license": name, "credit": "Panoramax",
                 "pano": bool(fov and fov >= 360), "lat": lat, "lon": lon}
    if lic := (url or _https(lic_link)):
        out["license_url"] = lic
    if people:
        out["author"] = str(people[-1])[:80]
    if (az := _num(props.get("view:azimuth"))) is not None:
        out["heading"] = round(az) % 360
    if props.get("datetime"):
        out["date"] = str(props["datetime"])[:10]
    return out


# --- Mapillary ---------------------------------------------------------------

MAPILLARY_FIELDS = "id,captured_at,compass_angle,computed_compass_angle,geometry,thumb_256_url,thumb_1024_url,creator,is_pano"


def _mapillary(pt: tuple[float, float], radius_m: int, token: str, get: Get | None, now_s: float) -> list[dict]:
    box = _bbox(pt, radius_m)

    def make() -> list[dict]:
        q = urllib.parse.urlencode({"bbox": ",".join(str(x) for x in box), "limit": ASK_N, "fields": MAPILLARY_FIELDS})
        d = _json(f"{MAPILLARY}?{q}", get, {"Authorization": f"OAuth {token}"})
        return [p for f in (d or {}).get("data") or [] if (p := _from_mapillary(f))]

    return _memo(f"mly|{box}", MAPILLARY_CACHE_S, now_s, make)


def _from_mapillary(f: dict) -> dict | None:
    pid = str(f.get("id") or "")
    coords = (f.get("geometry") or {}).get("coordinates") or []
    thumb = _https(f.get("thumb_256_url")) or _https(f.get("thumb_1024_url"))
    if not _ID.match(pid) or len(coords) < 2 or not thumb:
        return None
    lon, lat = _num(coords[0]), _num(coords[1])
    if lat is None or lon is None:
        return None
    out: dict = {"source": "mapillary", "id": pid, "thumb": thumb, "image": _https(f.get("thumb_1024_url")) or thumb,
                 "link": f"https://www.mapillary.com/app/?pKey={pid}&focus=photo",
                 "license": "CC BY-SA 4.0", "license_url": "https://creativecommons.org/licenses/by-sa/4.0/",
                 "credit": "Mapillary", "pano": bool(f.get("is_pano")), "lat": lat, "lon": lon}
    if who := (f.get("creator") or {}).get("username"):
        out["author"] = str(who)[:80]
    az = _num(f.get("computed_compass_angle"))
    az = az if az is not None else _num(f.get("compass_angle"))
    if az is not None:
        out["heading"] = round(az) % 360
    if (ms := _num(f.get("captured_at"))) is not None:
        out["date"] = datetime.fromtimestamp(ms / 1000, timezone.utc).strftime("%Y-%m-%d")
    return out


# --- the answers -------------------------------------------------------------

def _point(s: Any) -> tuple[float, float] | None:
    parts = str(s or "").split(",")
    if len(parts) != 2:
        return None
    la, lo = _num(parts[0]), _num(parts[1])
    if la is None or lo is None or not (-90 <= la <= 90 and -180 <= lo <= 180):
        return None
    return la, lo


def _int(v: Any, default: int, lo: int, hi: int) -> int | None:
    if v in (None, ""):
        return default
    n = _num(v)
    return None if n is None or not lo <= n <= hi else int(n)


def _rank(photos: list[dict], pt: tuple[float, float], heading: float | None, radius_m: int) -> list[dict]:
    """Within the radius; those looking the right way first, then nearest first."""
    out = []
    for p in photos:
        d = _distance_m(pt, (p["lat"], p["lon"]))
        if d > radius_m:
            continue
        want = heading if heading is not None else (_bearing((p["lat"], p["lon"]), pt) if d >= 3 else None)
        facing = p["pano"] or want is None or (p.get("heading") is not None and _off(p["heading"], want) <= FACING_DEG)
        out.append({**p, "distance_m": round(d), "facing": bool(facing)})
    return sorted(out, key=lambda p: (not p["facing"], p["distance_m"]))


def street_photos(near: str = "", radius_m: Any = None, heading: Any = None, limit: Any = None, *,
                  get: Get | None = None, now: float | None = None) -> tuple[bool, dict]:
    now_s = time.time() if now is None else now
    pt = _point(near)
    if pt is None:
        return False, {"status": 400, "code": "bad_point", "error": "near is lat,lon"}
    radius = _int(radius_m, RADIUS_M, 5, RADIUS_MAX_M)
    n = _int(limit, LIMIT, 1, LIMIT_MAX)
    head = None if heading in (None, "") else _num(heading)
    if radius is None or n is None or (heading not in (None, "") and (head is None or not 0 <= head < 360)):
        return False, {"status": 400, "code": "bad_ask", "error": f"radius_m 5-{RADIUS_MAX_M}, limit 1-{LIMIT_MAX}, heading 0-359"}
    found: list[dict] = []
    sources: dict = {}
    try:
        found += _panoramax(pt, radius, get, now_s)
        sources["panoramax"] = "ok"
    except (_Failed, OSError, ValueError) as e:
        _failures.swallowed(e)
        sources["panoramax"] = "failed"
    token = mapillary_token()
    if not token:
        sources["mapillary"] = "off"
    else:
        try:
            found += _mapillary(pt, radius, token, get, now_s)
            sources["mapillary"] = "ok"
        except (_Failed, OSError, ValueError) as e:
            _failures.swallowed(e)
            sources["mapillary"] = "failed"
    return True, {"photos": _rank(found, pt, head, radius)[:n], "sources": sources}


def along(points: str = "", radius_m: Any = None, *, get: Get | None = None,
          now: float | None = None) -> tuple[bool, dict]:
    """The best Panoramax photo at each point (`lat,lon[,heading]` joined by `;`), or null.

    Mapillary is not asked: its terms (§5) forbid use in connection with
    route guidance."""
    now_s = time.time() if now is None else now
    raw = [s for s in str(points or "").split(";") if s.strip()]
    radius = _int(radius_m, ALONG_RADIUS_M, 5, 200)
    if not raw or len(raw) > ALONG_MAX or radius is None:
        return False, {"status": 400, "code": "bad_points", "error": f"points: 1-{ALONG_MAX} of lat,lon[,heading] joined by ;"}
    asks: list[tuple[tuple[float, float], float | None]] = []
    for s in raw:
        parts = s.split(",")
        pt = _point(",".join(parts[:2]))
        head = _num(parts[2]) if len(parts) == 3 else None
        if pt is None or len(parts) > 3 or (len(parts) == 3 and (head is None or not 0 <= head < 360)):
            return False, {"status": 400, "code": "bad_points", "error": f"not a point: {s[:40]}"}
        asks.append((pt, head))
    out: list[dict | None] = []
    for pt, head in asks:
        try:
            best = _rank(_panoramax(pt, radius, get, now_s), pt, head, radius)
        except (_Failed, OSError, ValueError) as e:
            _failures.swallowed(e)
            best = []
        # Along a road, only a photo looking the way the route goes.
        out.append(best[0] if best and (best[0]["facing"] or head is None) else None)
    return True, {"points": out}
