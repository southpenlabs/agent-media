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
"""

from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request

DEFAULT_URL = "https://nominatim.openstreetmap.org"
USER_AGENT = "agent-media place find (https://github.com/southpenlabs/agent-media)"
TIMEOUT_S = 10
#: Half the side of the box a search leans towards, in degrees (about 20 km).
NEAR_SPAN = 0.2


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
    """The best matches, best first: `{"name", "address", "lat", "lon", "kind"}`."""
    q = {"q": words, "format": "jsonv2", "limit": str(max(1, min(limit, 10))),
         "addressdetails": "0"}
    if near:
        lat, lon = near
        q["viewbox"] = f"{lon - NEAR_SPAN},{lat + NEAR_SPAN},{lon + NEAR_SPAN},{lat - NEAR_SPAN}"
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
        out.append({"name": name, "address": address, "lat": lat, "lon": lon,
                    "kind": str(r.get("type") or r.get("category") or "")})
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
