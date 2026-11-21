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
shop's own name first (`NICKNAMES`, then the brand nicknames kept from
NSI and Wikidata, place_nicknames.py), and a search near a point asks
inside the box around it before anywhere at all: the viewbox only leans,
and an exact "Woolies" in England beats every Woolworths nearby (David, 22
Nov 2026: "when you search for woolies it doesn't find Woolworths"). A
search that finds nothing anywhere is reworded once by a fast model
(`reword`, MEDIA_PLACE_REWORD=0 for none) and asked again: what Google's
own search does with "bunnos".
"""

from __future__ import annotations

import json
import os
import re
import time
import urllib.parse
import urllib.request
from typing import Callable

from . import failures

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
#: Seconds a model has to reword a search that found nothing.
REWORD_TIMEOUT_S = 8


def expand(words: str, table: dict[str, str] | None = None) -> str:
    """`words` with any everyday name put back to the shop's own: "woolies
    rosebud" -> "Woolworths rosebud". The list above first, then the brand
    nicknames from open data (place_nicknames: NSI and Wikidata)."""
    out = _NICK_RE.sub(lambda m: NICKNAMES[m.group(1).lower()], words)
    if out != words:
        return out
    from . import place_nicknames

    if table is None:
        place_nicknames.refresh_if_stale()
        table = place_nicknames.table()
    if not table:
        return words
    toks = words.split()
    norm = [place_nicknames.norm(t) for t in toks]
    res, i = [], 0
    while i < len(toks):
        for n in range(min(4, len(toks) - i), 0, -1):
            key = " ".join(w for w in norm[i:i + n] if w)
            if key and key in table:
                res.append(table[key])
                i += n
                break
        else:
            res.append(toks[i])
            i += 1
    return " ".join(res)


#: What a model is asked when a search finds nothing at all.
REWORD_PROMPT = (
    "You turn what a person typed into a map search into the name the place "
    "would have on OpenStreetMap: a shop's or chain's proper name for a "
    "nickname or abbreviation (\"maccas\" -> \"McDonald's\"), a landmark's "
    "proper name, spelling fixed. Keep any suburb or street they gave. Reply "
    "with the search words only, or NONE when you cannot improve them."
)


def reword(words: str, ask: Callable[[str, str], str | None] | None = None) -> str:
    """A model's better search words for a search that found nothing, or ""."""
    if os.environ.get("MEDIA_PLACE_REWORD", "1").strip() == "0":
        return ""
    if ask is None:
        from .intake import fast

        def ask(system: str, user: str) -> str | None:
            return fast.chat(system, user, REWORD_TIMEOUT_S, purpose_env="MEDIA_PLACE_REWORD_MODEL")
    try:
        got = ask(REWORD_PROMPT, words) or ""
    except Exception as e:  # noqa: BLE001 - no model: the search found nothing
        failures.swallowed(e)
        return ""
    line = " ".join((got.strip().splitlines() or [""])[0].split()).strip("\"'`*")
    if not line or line.upper() == "NONE" or len(line) > 120 or line.lower() == words.lower():
        return ""
    return line


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


def find(words: str, near: tuple[float, float] | None = None, limit: int = 5,
         ask: Callable[[str, str], str | None] | None = None) -> list[dict]:
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
    # Nothing anywhere: a model's rewording ("bunnos" -> "Bunnings"), asked once.
    better = reword(words, ask)
    if not better:
        return []
    for w, bounded in ([(better, True), (better, False)] if near else [(better, False)]):
        if gap:
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
