"""Victoria's phone and seatbelt camera sites on the Map (maps proposal step
3; David, 10 Nov 2026: "what about mobile phone and seatbelt cameras?").

Victoria publishes the approved sites of its distracted driver and seatbelt
detection cameras monthly, as a spreadsheet of road and suburb (vic.gov.au,
CC BY 4.0): the cameras are on trailers and may be anywhere along that road,
on any day. So each site is drawn as the road in that suburb, a line, never
a guessed point.

Off unless `MEDIA_MAP_VIC=1` (a server in Victoria). `python -m
agent_media_server.map_vic refresh` (a daily timer: it does nothing until
the page links a new file) finds the newest spreadsheet on PAGE, reads it
(no spreadsheet library: an .xlsx is zipped XML), finds each road's line
with Nominatim's structured search (one a second, kept for good in the cache
dir, a miss asked again after 30 days) and keeps the sites in
`vic-cameras.json` in the state dir. The layer is read for the box on screen
like the OSM cameras.
"""

from __future__ import annotations

import datetime as dt
import io
import json
import os
import re
import time
import urllib.parse
import urllib.request
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path

PAGE = "https://www.vic.gov.au/distracted-driver-and-seatbelt-detection-camera-locations"
SITE = "https://www.vic.gov.au"
GEOCODER = "https://nominatim.openstreetmap.org"
USER_AGENT = "agent-media map (https://github.com/southpenlabs/agent-media)"
SOURCE = "Victorian Government, CC BY 4.0; roads © OpenStreetMap contributors"
MISS_AGAIN_S = 30 * 86400

LAYER = {"id": "vic_phone_seatbelt", "name": "Phone & seatbelt cameras", "kind": "lines",
         "icon": "camera", "colour": "#d9a400", "default_on": False, "bbox": True, "min_zoom": 9,
         "source": SOURCE,
         "note": "Approved sites, published monthly: a trailer camera may be anywhere along the road, any day."}

_NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}


def enabled() -> bool:
    return (os.environ.get("MEDIA_MAP_VIC") or "").lower() in ("1", "on", "yes", "true")


def layers() -> list[dict]:
    return [dict(LAYER)] if enabled() else []


def _state() -> Path:
    base = os.environ.get("XDG_STATE_HOME")
    d = (Path(base) if base else Path.home() / ".local" / "state") / "agent-media"
    d.mkdir(parents=True, exist_ok=True)
    return d / "vic-cameras.json"


def _cache() -> Path:
    base = os.environ.get("XDG_CACHE_HOME")
    d = (Path(base) if base else Path.home() / ".cache") / "agent-media"
    d.mkdir(parents=True, exist_ok=True)
    return d / "vic-roads.json"


def _get(url: str, timeout: int = 60) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as res:
        return res.read()


def newest_file(page_html: str) -> str:
    """The newest spreadsheet the page links ("…_November-2026.xlsx"), as a full address."""
    links = sorted(set(re.findall(r'(?:https://www\.vic\.gov\.au)?(/sites/default/files/[^"\'\s>]+\.xlsx)', page_html)))
    if not links:
        return ""
    # The folder is the upload month (2026-10/…November-2026): the latest folder wins.
    return SITE + max(links, key=lambda p: (p.split("/")[4] if len(p.split("/")) > 4 else "", p))


def read_sites(xlsx: bytes) -> tuple[list[dict], float | None]:
    """`([{road, suburb, count, reason}], effective)` from the spreadsheet: one
    row per site, the same road and suburb counted together. `effective`:
    the latest Effective Date (epoch seconds), or None."""
    z = zipfile.ZipFile(io.BytesIO(xlsx))
    strings = []
    if "xl/sharedStrings.xml" in z.namelist():
        for si in ET.fromstring(z.read("xl/sharedStrings.xml")).findall("m:si", _NS):
            strings.append("".join(t.text or "" for t in si.iter(f"{{{_NS['m']}}}t")))
    sheet = ET.fromstring(z.read("xl/worksheets/sheet1.xml"))
    rows = []
    for r in sheet.findall(".//m:row", _NS):
        cells = []
        for c in r.findall("m:c", _NS):
            if c.get("t") == "inlineStr":
                cells.append("".join(t.text or "" for t in c.iter(f"{{{_NS['m']}}}t")))
                continue
            v = c.find("m:v", _NS)
            raw = v.text if v is not None else ""
            cells.append(strings[int(raw)] if c.get("t") == "s" and raw else (raw or ""))
        rows.append([str(x).strip() for x in cells])
    head = next((i for i, r in enumerate(rows) if r[:2] and r[0].lower() == "road"), None)
    if head is None:
        return [], None
    sites: dict[tuple[str, str], dict] = {}
    latest = None
    for r in rows[head + 1:]:
        if len(r) < 2 or not r[0] or not r[1]:
            continue
        road, suburb = " ".join(r[0].split()), " ".join(r[1].split()).title()
        site = sites.setdefault((road.lower(), suburb.lower()),
                                {"road": road, "suburb": suburb, "count": 0, "reason": r[2] if len(r) > 2 else ""})
        site["count"] += 1
        try:
            day = (dt.datetime(1899, 12, 30, tzinfo=dt.timezone.utc)
                   + dt.timedelta(days=float(r[3]))).timestamp()
            latest = max(latest or 0, day)
        except (IndexError, ValueError):
            pass
    return list(sites.values()), latest


def _road_line(road: str, suburb: str) -> dict | None:
    """The road's geometry in the suburb from Nominatim, or None."""
    q = urllib.parse.urlencode({"street": road, "city": suburb, "state": "Victoria",
                                "country": "Australia", "format": "jsonv2",
                                "polygon_geojson": "1", "polygon_threshold": "0.0001", "limit": "1"})
    got = json.loads(_get(f"{os.environ.get('MEDIA_GEOCODER_URL') or GEOCODER}/search?{q}", 20) or b"[]")
    if not got:
        return None
    g = got[0].get("geojson") or {}
    if g.get("type") in ("LineString", "MultiLineString", "Point"):
        return g
    return {"type": "Point", "coordinates": [float(got[0]["lon"]), float(got[0]["lat"])]}


def refresh(force: bool = False) -> dict:
    """Bring vic-cameras.json up to the newest list on the page."""
    url = newest_file(_get(PAGE, 30).decode("utf-8", "replace"))
    if not url:
        raise SystemExit("no spreadsheet linked on " + PAGE)
    path = _state()
    try:
        kept = json.loads(path.read_text())
    except (OSError, ValueError):
        kept = {}
    if kept.get("file") == url and not force:
        return {"file": url, "sites": len(kept.get("features") or []), "unchanged": True}
    sites, effective = read_sites(_get(url))
    try:
        roads = json.loads(_cache().read_text())
    except (OSError, ValueError):
        roads = {}
    feats, missed = [], 0
    for s in sites:
        key = f"{s['road']}|{s['suburb']}".lower()
        hit = roads.get(key)
        if hit is None or (hit.get("geom") is None and time.time() - hit.get("at", 0) > MISS_AGAIN_S):
            try:
                hit = {"geom": _road_line(s["road"], s["suburb"]), "at": time.time()}
            except Exception:  # noqa: BLE001 - asked again next run
                hit = None
            if hit is not None:
                roads[key] = hit
            time.sleep(1.1)  # Nominatim: one a second
        geom = (hit or {}).get("geom")
        if not geom:
            missed += 1
            continue
        where = f"{s['road']}, {s['suburb']}"
        feats.append({"type": "Feature", "geometry": geom,
                      "properties": {"title": "Phone & seatbelt camera site",
                                     "detail": where + (f" ({s['count']} sites)" if s["count"] > 1 else "")}})
    _cache().write_text(json.dumps(roads))
    out = {"at": time.time(), "as_of": effective or time.time(), "file": url, "features": feats,
           "missed": missed}
    part = path.with_suffix(".tmp")
    part.write_text(json.dumps(out))
    os.replace(part, path)
    return {"file": url, "sites": len(sites), "drawn": len(feats), "missed": missed}


def _coords(geom: dict):
    c = geom.get("coordinates") or []
    if geom.get("type") == "Point":
        yield c
    elif geom.get("type") == "LineString":
        yield from c
    elif geom.get("type") == "MultiLineString":
        for part in c:
            yield from part


def read(bbox) -> tuple[list[dict], dict]:
    if bbox is None:
        return [], {"zoom_in": True}
    try:
        kept = json.loads(_state().read_text())
    except (OSError, ValueError):
        return [], {"missing": True}
    w, s, e, n = bbox
    feats = [f for f in kept.get("features") or []
             if any(s <= lat <= n and w <= lon <= e for lon, lat in _coords(f["geometry"]))]
    return feats, {"partial": False, "as_of": round(kept.get("as_of") or 0)}


if __name__ == "__main__":
    import sys

    if sys.argv[1:2] != ["refresh"]:
        raise SystemExit("usage: python -m agent_media_server.map_vic refresh [--force]")
    from agent_media_core.intake._env import load_env_file

    load_env_file("map-vic")
    print(json.dumps(refresh(force="--force" in sys.argv)))
