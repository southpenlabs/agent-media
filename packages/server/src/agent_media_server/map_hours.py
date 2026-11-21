"""A place's opening hours when OpenStreetMap has none: Foursquare's
(David, 21 Nov 2026: "how does Google maps get blue fin opening hours?" …
"Can we do one and two?" — two being a Foursquare fallback).

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


def _key() -> str:
    return (os.environ.get("MEDIA_FOURSQUARE_KEY") or "").strip()


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
                now=None) -> tuple[bool, dict]:
    try:
        la, lo = float(lat), float(lon)
    except (TypeError, ValueError):
        return False, {"status": 400, "code": "bad_point", "error": "lat and lon are numbers"}
    name = " ".join((name or "").split())[:120]
    if not name or not (-90 <= la <= 90 and -180 <= lo <= 180):
        return False, {"status": 400, "code": "bad_place", "error": "give a name, lat and lon"}
    if not _key():
        return True, {"found": False, "reason": "no_key"}
    try:
        got = _ask({"ll": f"{la:.6f},{lo:.6f}", "query": name, "radius": NEAR_M, "limit": 5,
                    "fields": "fsq_place_id,name,latitude,longitude,hours"}, fetch)
    except Exception as e:  # noqa: BLE001 - Foursquare down, a bad key, a timeout
        _failures.swallowed(e)
        return True, {"found": False, "reason": "failed"}
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
        return True, {"found": False, "reason": "no_match"}
    fid = str(best.get("fsq_place_id") or "")
    if fid:
        with _lock:
            _ids[key] = fid  # the one thing Foursquare lets us keep
    hours = best.get("hours") if isinstance(best.get("hours"), dict) else {}
    oh = osm_hours(hours.get("regular") or [])
    if not oh:
        return True, {"found": False, "reason": "no_hours"}
    out = {"found": True, "source": "foursquare", "opening_hours": oh,
           "display": str(hours.get("display") or ""),
           "link": f"https://foursquare.com/v/{urllib.parse.quote(fid)}" if fid else "https://foursquare.com"}
    from . import map_places

    open_now, changes = map_places.open_state(oh, la, lo, now)
    if open_now is None and isinstance(hours.get("open_now"), bool):
        open_now = hours["open_now"]
    out["open_now"] = open_now
    if changes:
        out["changes_at"] = changes
    return True, out


def forget() -> None:
    with _lock:
        _ids.clear()
