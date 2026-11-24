"""The audio tour: what is worth hearing about near a point (maps proposal step 10, "Audio
tour"; docs/proposals/2026-11-06-maps.md).

David, 24 Nov 2026 ("Go on with the audio tour"): as you walk or drive, the app's voice
tells you about what you pass. The Map asks here as it moves and says the next place
ahead it has not said yet; "What's that?" says the best one ahead now.

    GET /map/tour/near?lat=&lon=&radius_m=(50-2000, default 400)&lang=(en)
        → {places: [{id, title, lat, lon, distance_m, bearing, text, link, credit,
                     license, license_url}], lang}

Wikipedia's own nearby search (list=geosearch, articles with coordinates), then their
lead sections cut to two sentences (prop=extracts), both in the app's language where
that Wikipedia has the article, English otherwise. Lists, streets' own articles and
articles with no lead are left out. Nearest first; `bearing` is from the point, degrees
from north.

Open source first: Wikipedia's geosearch is what every "nearby" app uses (the Wikipedia
app's Nearby, WikiShootMe); asked as plain JSON through map_wiki's lock and User-Agent,
one request at a time. Cached per ~100 m cell and language as the place cards are
(map_wiki's cache: a month, a miss three days). Text CC BY-SA 4.0, credited.
"""
from __future__ import annotations

import math
import re
import time
import urllib.parse
from typing import Any

from . import map_wiki
from .map_wiki import Get

RADIUS_DEFAULT = 400
RADIUS_MIN = 50
RADIUS_MAX = 2000
LIMIT = 20
#: Sentences in what is said; the text is cut to this many characters as well.
SENTENCES = 2
TEXT_MAX = 360
CELL_DP = 3
_LANG = re.compile(r"^[a-z]{2,3}$")
#: Titles that are not a place to tell about.
_SKIP = re.compile(r"^(List of|Lists of|Timeline of|History of|Outline of)\b|\((disambiguation)\)$", re.I)
#: A street, road or line has an article, but a tour does not read it at every corner.
_ROADLIKE = re.compile(r"\b(Street|Road|Avenue|Highway|Freeway|Parade|Lane|Boulevard|Railway line|line)$")


def _api(lang: str) -> str:
    return f"https://{lang}.wikipedia.org/w/api.php"


def bearing(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lon2 - lon1)
    x = math.sin(dl) * math.cos(p2)
    y = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return (math.degrees(math.atan2(x, y)) + 360) % 360


def _metres(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = (math.sin((p2 - p1) / 2) ** 2
         + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2)
    return 2 * r * math.asin(math.sqrt(min(1.0, a)))


def _sentences(text: str, n: int) -> str:
    text = " ".join(text.split())
    # Pronunciation and dates in brackets right after the name read badly aloud.
    text = re.sub(r"\s*\([^()]{0,120}\)", "", text, count=1)
    parts = re.split(r"(?<=[.!?])\s+(?=[A-Z0-9\"'“])", text)
    return map_wiki._trim(" ".join(parts[:n]), TEXT_MAX)


def _cell(lat: float, lon: float, radius: int, lang: str) -> str:
    return f"tour:{lang}:{round(lat, CELL_DP)},{round(lon, CELL_DP)}:{radius}"


def _ask(lat: float, lon: float, radius: int, lang: str, get: Get | None) -> list[dict]:
    q = {"action": "query", "list": "geosearch", "gscoord": f"{lat:.5f}|{lon:.5f}", "gsradius": str(radius),
         "gslimit": str(LIMIT), "gsnamespace": "0", "format": "json", "formatversion": "2"}
    d = map_wiki._json(_api(lang) + "?" + urllib.parse.urlencode(q), get)
    hits = [h for h in ((d or {}).get("query") or {}).get("geosearch") or []
            if isinstance(h, dict) and h.get("pageid") and not _SKIP.search(str(h.get("title") or ""))
            and not _ROADLIKE.search(str(h.get("title") or ""))]
    if not hits:
        return []
    q = {"action": "query", "prop": "extracts|info", "exintro": "1", "explaintext": "1", "exlimit": "max",
         "inprop": "url", "pageids": "|".join(str(h["pageid"]) for h in hits), "format": "json", "formatversion": "2"}
    d = map_wiki._json(_api(lang) + "?" + urllib.parse.urlencode(q), get)
    pages = {p.get("pageid"): p for p in ((d or {}).get("query") or {}).get("pages") or [] if isinstance(p, dict)}
    out = []
    for h in hits:
        p = pages.get(h["pageid"]) or {}
        text = _sentences(str(p.get("extract") or ""), SENTENCES)
        if not text:
            continue
        out.append({"id": f"{lang}:{h['pageid']}", "title": str(h["title"]), "lat": float(h["lat"]), "lon": float(h["lon"]),
                    "text": text,
                    "link": str(p.get("fullurl") or f"https://{lang}.wikipedia.org/?curid={h['pageid']}"),
                    "credit": "Wikipedia", "license": map_wiki.CC_BY_SA, "license_url": map_wiki.CC_BY_SA_URL})
    return out


def near(lat: Any, lon: Any, radius: Any = None, lang: str = "en", get: Get | None = None,
         now_s: float | None = None) -> tuple[bool, dict]:
    pt = map_wiki._point(lat, lon)
    if pt is None:
        return False, {"status": 400, "code": "bad_point", "error": "lat and lon, in range"}
    try:
        r = int(float(radius)) if radius not in (None, "") else RADIUS_DEFAULT
    except (TypeError, ValueError):
        return False, {"status": 400, "code": "bad_radius", "error": "radius_m: metres"}
    r = max(RADIUS_MIN, min(RADIUS_MAX, r))
    lang = (lang or "en").lower().split("-")[0]
    if not _LANG.match(lang):
        lang = "en"
    now_s = time.time() if now_s is None else now_s
    la, lo = pt
    # The cell's centre is asked, so one answer serves the whole cell; its radius is a
    # little wider so a place near the cell's edge is not missed.
    cla, clo = round(la, CELL_DP), round(lo, CELL_DP)

    def fetch(lg: str) -> list[dict]:
        got = map_wiki._memo(_cell(la, lo, r, lg), now_s, lambda: _ask(cla, clo, min(10000, r + 100), lg, get))
        return got if isinstance(got, list) else []

    try:
        places = fetch(lang)
        used = lang
        if not places and lang != "en":
            places, used = fetch("en"), "en"
    except map_wiki._Failed as e:
        return False, {"status": 502, "code": "unreachable", "error": f"Wikipedia did not answer: {e}"}
    out = []
    for p in places:
        d = _metres(la, lo, p["lat"], p["lon"])
        if d > r:
            continue
        out.append({**p, "distance_m": round(d), "bearing": round(bearing(la, lo, p["lat"], p["lon"]))})
    out.sort(key=lambda p: p["distance_m"])
    return True, {"places": out, "lang": used}
