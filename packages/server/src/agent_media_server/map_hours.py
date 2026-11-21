"""A place's opening hours when OpenStreetMap has none: Google's, then
Foursquare's (David, 21 Nov 2026: "how does Google maps get blue fin
opening hours?" … "Can we do one and two?" — two being a Foursquare
fallback; 22 Nov: "Can we also get data from Google on a free tier?").

Google Places API (New), first when `MEDIA_GOOGLE_PLACES_KEY` is set: a
Text Search with only `places.id,places.displayName,places.location` in the
field mask finds the place (no Enterprise field, so not the dear call),
its place ID is kept (Google lets place IDs be stored indefinitely: in
memory and `state_dir()/google-place-ids.json`), and Place Details with
`regularOpeningHours,googleMapsUri` gives the hours — the one Enterprise
call, 1,000 free a month since March 2025. Those calls are counted per
calendar month in `state_dir()/google-places-usage.json`, and at
`MEDIA_GOOGLE_PLACES_MONTHLY_MAX` (900 unless set) Google is not asked
again that month, so the free calls are never passed. Hours are not
stored. The app credits "Google Maps" and links the place's page.

`GET /map/place_hours?name=<name>&lat=<lat>&lon=<lon>` (§6.50), asked by
the Map when a place's card opens with no hours of OSM's own. →
`{found: true, source: "foursquare", opening_hours (as OSM writes them),
display (Foursquare's own words), open_now, changes_at?, link}` or
`{found: false, reason}` (`no_key`, `no_match`, `no_hours`, `failed`).

Foursquare Places API (places-api.foursquare.com, `X-Places-Api-Version:
2025-06-17`, a Service Key as `Authorization: Bearer`), Place Search with
`fields=fsq_place_id,name,hours` near the point. Hours are a Premium field:
each card opened is one billed call, so nothing here runs for a list.

Their terms (usage guidelines, Pay as You Go & Sandbox): no caching of
anything but `fsq_place_id`. So the hours are asked afresh each time and
never stored; only which Foursquare place a name and point matched is kept
(in memory), so a second opening goes straight to it. The app credits
Foursquare beside the hours (visual-crediting policy).

Off unless `MEDIA_FOURSQUARE_KEY` holds a Service Key.

Cached (David, 22 Nov 2026: "let's cache opening hours", choosing "Cache
everywhere for a week" when told it goes against both services' terms):
each answer is kept in `state_dir()/place-hours-cache.json` by name and
point (rounded to about 10 m), found hours for CACHE_S (a week), "no
match" and "no hours" for MISS_S (a day), a failure or a missing key never.
Whether a place is open now is worked out again from the kept hours on each
read. Google's terms allow storing place IDs only, and Foursquare's only
`fsq_place_id`, so a public release must turn this off (outside-services
rows for Google Places and Foursquare say so).
"""
from __future__ import annotations

import json
import math
import os
import re
import threading
import urllib.parse
import urllib.request
from typing import Callable

from agent_media_core import failures as _failures

BASE = "https://places-api.foursquare.com"
VERSION = "2025-06-17"
TIMEOUT_S = 6
#: How far from the point a Foursquare place may be and still be this one.
NEAR_M = 150
#: Foursquare day numbers (1 = Monday) as OSM writes days.
DAYS = {1: "Mo", 2: "Tu", 3: "We", 4: "Th", 5: "Fr", 6: "Sa", 7: "Su"}

_ids: dict[tuple, str] = {}
_lock = threading.Lock()
#: How long kept hours are used before asking again; a miss for a day.
CACHE_S = 7 * 86400
MISS_S = 86400
CACHE_FILE = "place-hours-cache.json"


def _cache_key(name: str, la: float, lo: float) -> str:
    return f"{name.lower()}|{la:.4f}|{lo:.4f}"


def _cached(key: str, now_s: float) -> dict | None:
    hit = _read(CACHE_FILE).get(key)
    if not isinstance(hit, dict) or not isinstance(hit.get("answer"), dict):
        return None
    keep = CACHE_S if hit["answer"].get("found") else MISS_S
    return dict(hit["answer"]) if now_s - float(hit.get("at") or 0) < keep else None


def _keep(key: str, answer: dict, now_s: float) -> None:
    if not answer.get("found") and answer.get("reason") not in ("no_match", "no_hours"):
        return
    with _lock:
        d = _read(CACHE_FILE)
        d = {k: v for k, v in d.items()
             if isinstance(v, dict) and now_s - float(v.get("at") or 0) < CACHE_S}
        d[key] = {"at": now_s, "answer": answer}
        _write(CACHE_FILE, d)

GOOGLE = "https://places.googleapis.com/v1"
#: Enterprise calls a month at most: the free 1,000 with room to spare.
GOOGLE_MONTHLY_MAX = 900
#: (method, url, json body or None, headers) → the JSON answer. Tests pass their own.
GFetch = Callable[[str, str, "dict | None", dict], dict]


def _key() -> str:
    return (os.environ.get("MEDIA_FOURSQUARE_KEY") or "").strip()


def _gkey() -> str:
    return (os.environ.get("MEDIA_GOOGLE_PLACES_KEY") or "").strip()


def _gmax() -> int:
    try:
        return int(os.environ.get("MEDIA_GOOGLE_PLACES_MONTHLY_MAX") or GOOGLE_MONTHLY_MAX)
    except ValueError:
        return GOOGLE_MONTHLY_MAX


def _state(name: str):
    from agent_media_core._paths import state_dir

    return state_dir() / name


def _read(name: str) -> dict:
    try:
        d = json.loads(_state(name).read_text())
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _write(name: str, d: dict) -> None:
    p = _state(name)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_name(f".{p.name}.{os.getpid()}")
        tmp.write_text(json.dumps(d))
        os.replace(tmp, p)
    except OSError as e:
        _failures.swallowed(e)


def _month(now) -> str:
    import datetime as dt

    return (now or dt.datetime.now(dt.timezone.utc)).strftime("%Y-%m")


def google_usage(now=None) -> int:
    """Enterprise calls made to Google this calendar month."""
    u = _read("google-places-usage.json")
    return int(u.get("calls") or 0) if u.get("month") == _month(now) else 0


def _google_spent(now=None) -> None:
    with _lock:
        m = _month(now)
        u = _read("google-places-usage.json")
        n = int(u.get("calls") or 0) if u.get("month") == m else 0
        _write("google-places-usage.json", {"month": m, "calls": n + 1})


def _gfetch(method: str, url: str, body: dict | None, headers: dict) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, headers={**headers, **({"Content-Type": "application/json"} if data else {})}, method=method)
    with urllib.request.urlopen(req, timeout=TIMEOUT_S) as res:
        return json.loads(res.read().decode("utf-8") or "{}")


def osm_hours_google(hours: dict) -> str:
    """Google's `regularOpeningHours` (periods of {open, close: {day 0 = Sunday,
    hour, minute}}) as an OSM opening_hours string; an open period with no
    close is open all the time ("24/7")."""
    periods = [p for p in (hours or {}).get("periods") or [] if isinstance(p, dict)]
    if any(isinstance(p.get("open"), dict) and not p.get("close") for p in periods):
        return "24/7"
    regular = []
    for p in periods:
        o, c = p.get("open") or {}, p.get("close") or {}
        if not isinstance(o.get("day"), int):
            continue
        day = 7 if o["day"] == 0 else o["day"]
        ch, cm = int(c.get("hour") or 0), int(c.get("minute") or 0)
        # Closing at midnight: OSM's 24:00.
        close = "2400" if (ch, cm) == (0, 0) else f"{ch:02d}{cm:02d}"
        regular.append({"day": day, "open": f"{int(o.get('hour') or 0):02d}{int(o.get('minute') or 0):02d}", "close": close})
    return osm_hours(regular)


def _google(name: str, la: float, lo: float, gfetch: GFetch | None, now) -> dict:
    """Google's hours, or `{found: False, reason}` to go on to Foursquare."""
    if not _gkey():
        return {"found": False, "reason": "no_key"}
    if google_usage(now) >= _gmax():
        return {"found": False, "reason": "google_month_spent"}
    fetch = gfetch or _gfetch
    head = {"X-Goog-Api-Key": _gkey()}
    key = f"{name.lower()}|{round(la, 4)}|{round(lo, 4)}"
    pid = _read("google-place-ids.json").get(key)
    try:
        if not pid:
            got = fetch("POST", f"{GOOGLE}/places:searchText",
                        {"textQuery": name, "maxResultCount": 5,
                         "locationBias": {"circle": {"center": {"latitude": la, "longitude": lo}, "radius": float(NEAR_M)}}},
                        {**head, "X-Goog-FieldMask": "places.id,places.displayName,places.location"})
            for r in got.get("places") or []:
                loc = r.get("location") or {}
                rl, ro = loc.get("latitude"), loc.get("longitude")
                if isinstance(rl, (int, float)) and isinstance(ro, (int, float)) and _distance_m(la, lo, rl, ro) > NEAR_M:
                    continue
                if _alike(name, str((r.get("displayName") or {}).get("text") or "")) and r.get("id"):
                    pid = str(r["id"])
                    break
            if not pid:
                return {"found": False, "reason": "no_match"}
            with _lock:
                ids = _read("google-place-ids.json")
                ids[key] = pid  # place IDs may be kept indefinitely
                _write("google-place-ids.json", ids)
        _google_spent(now)
        d = fetch("GET", f"{GOOGLE}/places/{urllib.parse.quote(pid)}", None,
                  {**head, "X-Goog-FieldMask": "regularOpeningHours,googleMapsUri"})
    except Exception as e:  # noqa: BLE001 - Google down, a bad key, over quota
        _failures.swallowed(e)
        return {"found": False, "reason": "failed"}
    hours = d.get("regularOpeningHours") if isinstance(d.get("regularOpeningHours"), dict) else {}
    oh = osm_hours_google(hours)
    if not oh:
        return {"found": False, "reason": "no_hours"}
    return {"found": True, "source": "google", "opening_hours": oh,
            "display": "; ".join(str(x) for x in hours.get("weekdayDescriptions") or []),
            "link": str(d.get("googleMapsUri") or f"https://www.google.com/maps/place/?q=place_id:{pid}")}


def _words(s: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", (s or "").lower()) if len(w) > 1}


def _alike(a: str, b: str) -> bool:
    """Names that are the same place: most of the shorter one's words in the other."""
    wa, wb = _words(a), _words(b)
    if not wa or not wb:
        return False
    small = min(len(wa), len(wb))
    return len(wa & wb) >= max(1, math.ceil(small * 0.6))


def _hhmm(t: str) -> str | None:
    digits = re.sub(r"\D", "", t or "")
    if len(digits) == 3:
        digits = "0" + digits
    if len(digits) != 4:
        return None
    h, m = int(digits[:2]), int(digits[2:])
    if not (0 <= h <= 24 and 0 <= m < 60):
        return None
    return f"{h:02d}:{m:02d}"


def osm_hours(regular: list) -> str:
    """Foursquare's `regular` (`[{day: 1–7, open: "1130", close: "2000"}]`)
    as an OSM opening_hours string: days with the same times grouped
    ("Mo-Fr 07:00-15:00; Sa,Su 08:00-14:00"), a close past midnight as 24+
    hours as OSM allows ("Fr 18:00-26:00" is written "Fr 18:00-02:00")."""
    spans: dict[int, list[str]] = {}
    for r in regular or []:
        if not isinstance(r, dict):
            continue
        day = r.get("day")
        o, c = _hhmm(str(r.get("open") or "")), _hhmm(str(r.get("close") or ""))
        if day not in DAYS or not o or not c:
            continue
        spans.setdefault(day, []).append(f"{o}-{c}")
    if not spans:
        return ""
    groups: list[tuple[list[int], str]] = []
    for day in sorted(spans):
        times = ",".join(sorted(spans[day]))
        if groups and groups[-1][1] == times and groups[-1][0][-1] == day - 1:
            groups[-1][0].append(day)
        else:
            groups.append(([day], times))
    parts = []
    for days, times in groups:
        d = DAYS[days[0]] if len(days) == 1 else (f"{DAYS[days[0]]},{DAYS[days[1]]}" if len(days) == 2
                                                  else f"{DAYS[days[0]]}-{DAYS[days[-1]]}")
        parts.append(f"{d} {times}")
    return "; ".join(parts)


def _ask(params: dict, fetch: Callable[[str, dict], dict] | None) -> dict:
    url = f"{BASE}/places/search?{urllib.parse.urlencode(params)}"
    headers = {"Authorization": f"Bearer {_key()}", "X-Places-Api-Version": VERSION, "Accept": "application/json"}
    if fetch:
        return fetch(url, headers)
    with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=TIMEOUT_S) as res:
        return json.loads(res.read().decode("utf-8") or "{}")


def _distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    kx = math.cos(math.radians((lat1 + lat2) / 2)) * 111_320.0
    return math.hypot((lon2 - lon1) * kx, (lat2 - lat1) * 110_540.0)


def place_hours(name: str, lat: str, lon: str, *, fetch: Callable[[str, dict], dict] | None = None,
                gfetch: GFetch | None = None, now=None) -> tuple[bool, dict]:
    try:
        la, lo = float(lat), float(lon)
    except (TypeError, ValueError):
        return False, {"status": 400, "code": "bad_point", "error": "lat and lon are numbers"}
    name = " ".join((name or "").split())[:120]
    if not name or not (-90 <= la <= 90 and -180 <= lo <= 180):
        return False, {"status": 400, "code": "bad_place", "error": "give a name, lat and lon"}
    import time as _time

    now_s = now.timestamp() if hasattr(now, "timestamp") else (float(now) if isinstance(now, (int, float)) else _time.time())
    key = _cache_key(name, la, lo)
    g = _cached(key, now_s)
    if g is None:
        g = _google(name, la, lo, gfetch, now)
        if not g.get("found"):
            f = _foursquare(name, la, lo, fetch)
            if f.get("found") or g.get("reason") == "no_key":
                g = f
        _keep(key, {k: v for k, v in g.items() if k not in ("open_now", "changes_at")}, now_s)
    else:
        g["cached"] = True
    if not g.get("found"):
        return True, g
    from . import map_places

    open_now, changes = map_places.open_state(g["opening_hours"], la, lo, now)
    given = g.pop("open_now_given", None)
    if open_now is None and isinstance(given, bool):
        open_now = given
    g["open_now"] = open_now
    if changes:
        g["changes_at"] = changes
    return True, g


def _foursquare(name: str, la: float, lo: float, fetch: Callable[[str, dict], dict] | None) -> dict:
    """Foursquare's hours, or `{found: False, reason}`."""
    if not _key():
        return {"found": False, "reason": "no_key"}
    try:
        got = _ask({"ll": f"{la:.6f},{lo:.6f}", "query": name, "radius": NEAR_M, "limit": 5,
                    "fields": "fsq_place_id,name,latitude,longitude,hours"}, fetch)
    except Exception as e:  # noqa: BLE001 - Foursquare down, a bad key, a timeout
        _failures.swallowed(e)
        return {"found": False, "reason": "failed"}
    key = (name.lower(), round(la, 4), round(lo, 4))
    with _lock:
        known = _ids.get(key)
    best = None
    for r in got.get("results") or []:
        if not isinstance(r, dict):
            continue
        # Near the point, always; then the one matched before, else the first with a like name.
        rl, ro = r.get("latitude"), r.get("longitude")
        if isinstance(rl, (int, float)) and isinstance(ro, (int, float)) and _distance_m(la, lo, rl, ro) > NEAR_M:
            continue
        if known and r.get("fsq_place_id") == known:
            best = r
            break
        if best is None and _alike(name, str(r.get("name") or "")):
            best = r
    if best is None:
        return {"found": False, "reason": "no_match"}
    fid = str(best.get("fsq_place_id") or "")
    if fid:
        with _lock:
            _ids[key] = fid  # the one thing Foursquare lets us keep
    hours = best.get("hours") if isinstance(best.get("hours"), dict) else {}
    oh = osm_hours(hours.get("regular") or [])
    if not oh:
        return {"found": False, "reason": "no_hours"}
    out = {"found": True, "source": "foursquare", "opening_hours": oh,
           "display": str(hours.get("display") or ""),
           "link": f"https://foursquare.com/v/{urllib.parse.quote(fid)}" if fid else "https://foursquare.com"}
    if isinstance(hours.get("open_now"), bool):
        out["open_now_given"] = hours["open_now"]
    return out


def forget() -> None:
    with _lock:
        _ids.clear()
        try:
            _state(CACHE_FILE).unlink(missing_ok=True)
        except OSError as e:
            _failures.swallowed(e)
