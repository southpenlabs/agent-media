"""`media place find <words>`: where a place is, for a reply's `[[map:]]`
marker (maps proposal step 2, docs/proposals/2026-11-06-maps.md).

An agent never guesses coordinates: it asks here and copies the line printed.
The answer comes from a Nominatim geocoder (OpenStreetMap data, ODbL),
`https://nominatim.openstreetmap.org` unless `MEDIA_GEOCODER_URL` names
another (a self-hosted one). Nominatim's policy is at most one request a
second with a named client, which a person's agent asking now and then keeps
well under.

`--near phone` leans the search towards the phone's shared location
(§6.21b, when it is shared and fresh), `--near lat,lon` towards a point: the
nearest of several cafés of one name, not one on another continent.

With a point, words that name a kind of place ("pharmacy", "open chemist",
"fuel", "toilets"; the server's list, `GET /map/places/kinds`) are asked of
the server's places search instead (`GET /map/places`, OpenStreetMap through
Overpass, §6.50): the nearest of that kind, with whether each is open now.
Words that name no kind, or a server out of reach, fall back to the
geocoder.

Nominatim matches names only as they are written in OpenStreetMap, so the
everyday names Google understands ("woolies", "maccas") are put back to the
shop's own name first (`NICKNAMES`), and a search near a point asks inside
the box around it before anywhere at all: the viewbox only leans, and an
exact "Woolies" in England beats every Woolworths nearby (David, 22 Nov
2026: "when you search for woolies it doesn't find Woolworths").
"""

from __future__ import annotations

import json
import os
import re
import time
import urllib.parse
import urllib.request

DEFAULT_URL = "https://nominatim.openstreetmap.org"
USER_AGENT = "agent-media place find (https://github.com/southpenlabs/agent-media)"
TIMEOUT_S = 10
#: Half the side of the box a search leans towards, in degrees (about 20 km).
NEAR_SPAN = 0.2


#: Everyday names for shops and places, put back to the name OpenStreetMap
#: has before the search. Lower case; matched as whole words.
NICKNAMES = {
    "woolies": "Woolworths", "woollies": "Woolworths", "woolys": "Woolworths",
    "maccas": "McDonald's", "macca's": "McDonald's", "macker's": "McDonald's",
    "hungry jacks": "Hungry Jack's", "dan murphys": "Dan Murphy's",
    "dan murphy": "Dan Murphy's", "danny murphys": "Dan Murphy's",
    "bunnos": "Bunnings", "chemmy": "Chemist Warehouse", "jb hifi": "JB Hi-Fi",
    "bottle o": "The Bottle-O", "bottle-o": "The Bottle-O",
    "spotty": "Spotlight", "the g": "Melbourne Cricket Ground",
}
_NICK_RE = re.compile(r"(?<![\w'-])(" + "|".join(
    re.escape(k) for k in sorted(NICKNAMES, key=len, reverse=True)) + r")(?![\w'-])", re.IGNORECASE)
#: Seconds between two asks of the public Nominatim (its policy: one a second).
PUBLIC_GAP_S = 1.0


def expand(words: str) -> str:
    """`words` with any everyday name put back to the shop's own: "woolies
    rosebud" -> "Woolworths rosebud"."""
    return _NICK_RE.sub(lambda m: NICKNAMES[m.group(1).lower()], words)


def _base() -> str:
    return (os.environ.get("MEDIA_GEOCODER_URL") or DEFAULT_URL).rstrip("/")


def near_point(near: str) -> tuple[float, float] | None:
    """`(lat, lon)` for `--near`: "phone" (its fresh shared fix) or "lat,lon"."""
    near = (near or "").strip()
    if not near:
        return None
    if near == "phone":
        from . import phone_ask

        loc = phone_ask.shared_location()
        return (float(loc["lat"]), float(loc["lon"])) if loc else None
    try:
        lat, lon = (float(x) for x in near.split(","))
    except ValueError:
        return None
    return (lat, lon) if -90 <= lat <= 90 and -180 <= lon <= 180 else None


def find(words: str, near: tuple[float, float] | None = None, limit: int = 5) -> list[dict]:
    """The best matches, best first: `{"name", "address", "lat", "lon", "kind",
    "opening_hours"?}` — the hours OSM's own string, where the place has them
    (Nominatim's extratags; David, 21 Nov 2026: "No opening hours? What about
    cafes and restaurants?")."""
    expanded = expand(words)
    tries = [(expanded, True), (expanded, False)] if near else [(expanded, False)]
    if expanded != words:  # a place really called "Woolies", when no Woolworths was found
        tries.append((words, False))
    gap = PUBLIC_GAP_S if _base() == DEFAULT_URL else 0.0
    for i, (w, bounded) in enumerate(tries):
        if i and gap:
            time.sleep(gap)
        out = _ask(w, near, limit, bounded)
        if out:
            return out
    return []


def _ask(words: str, near: tuple[float, float] | None, limit: int, bounded: bool) -> list[dict]:
    q = {"q": words, "format": "jsonv2", "limit": str(max(1, min(limit, 10))),
         "addressdetails": "0", "extratags": "1"}
    if near:
        lat, lon = near
        q["viewbox"] = f"{lon - NEAR_SPAN},{lat + NEAR_SPAN},{lon + NEAR_SPAN},{lat - NEAR_SPAN}"
        if bounded:
            q["bounded"] = "1"
    req = urllib.request.Request(f"{_base()}/search?{urllib.parse.urlencode(q)}",
                                 headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=TIMEOUT_S) as res:
        rows = json.loads(res.read().decode("utf-8") or "[]")
    out = []
    for r in rows if isinstance(rows, list) else []:
        try:
            lat, lon = round(float(r["lat"]), 6), round(float(r["lon"]), 6)
        except (KeyError, TypeError, ValueError):
            continue
        address = str(r.get("display_name") or "")
        name = str(r.get("name") or "") or address.split(",")[0].strip()
        row = {"name": name, "address": address, "lat": lat, "lon": lon,
               "kind": str(r.get("type") or r.get("category") or "")}
        # Its OpenStreetMap element, so the Map can add hours to it (osm_edit).
        if r.get("osm_type") in ("node", "way", "relation") and str(r.get("osm_id") or "").isdigit():
            row["id"] = f"{r['osm_type']}/{r['osm_id']}"
        hours = (r.get("extratags") or {}).get("opening_hours") if isinstance(r.get("extratags"), dict) else None
        if hours:
            row["opening_hours"] = str(hours)
        out.append(row)
    return out


#: Words dropped before the rest is taken as a kind: "nearest open pharmacy".
_FILLER = {"a", "an", "the", "nearest", "closest", "near", "nearby", "me", "here", "now",
           "open", "any", "some", "find"}
KIND_RADIUS_M = 5000


def find_kind(words: str, near: tuple[float, float], limit: int = 5,
              call=None) -> list[dict] | None:
    """The nearest places of the kind the words name, from the server's
    `GET /map/places`, nearest first, each with `distance_m` and `open_now`;
    "open" among the words keeps only the open ones. None when the words name
    no kind (the server's 400) or the server does not answer."""
    if call is None:
        from .phone_ask import _call as call
    toks = [w for w in words.lower().replace(",", " ").split()]
    kind = " ".join(w for w in toks if w not in _FILLER)
    if not kind:
        return None
    q = {"near": f"{near[0]},{near[1]}", "kind": kind, "radius_m": str(KIND_RADIUS_M),
         "limit": str(max(1, min(limit, 20)))}
    if "open" in toks:
        q["open_now"] = "1"
    try:
        code, obj = call("GET", f"/map/places?{urllib.parse.urlencode(q)}")
    except (OSError, ValueError):
        return None
    if code != 200:
        return None
    out = []
    for p in obj.get("places") or []:
        bits = [p.get("address") or "", f"{p.get('distance_m', 0)} m"]
        if p.get("open_now") is not None:
            bits.append("open" if p["open_now"] else "closed")
        if p.get("opening_hours"):
            bits.append(p["opening_hours"])
        out.append({"name": p.get("name") or "", "address": " · ".join(b for b in bits if b),
                    "lat": round(float(p["lat"]), 6), "lon": round(float(p["lon"]), 6),
                    "kind": p.get("kind") or "", "distance_m": p.get("distance_m"),
                    "open_now": p.get("open_now")})
    return out


def marker_part(row: dict) -> str:
    """The row as one part of a `[[map:]]` marker: `name @ lat,lon`."""
    name = row["name"].replace("|", "/").replace("]", ")").replace("@", "at")
    return f"{name} @ {row['lat']},{row['lon']}"
