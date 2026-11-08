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


def marker_part(row: dict) -> str:
    """The row as one part of a `[[map:]]` marker: `name @ lat,lon`."""
    name = row["name"].replace("|", "/").replace("]", ")").replace("@", "at")
    return f"{name} @ {row['lat']},{row['lon']}"
