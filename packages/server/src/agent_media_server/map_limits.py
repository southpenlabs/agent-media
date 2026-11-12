"""Speed limits for the Map (David, 12 Nov 2026: "let's add speed limits").

OpenStreetMap's `maxspeed` on roads, kept from the same weekly extract as the
cameras (map_osm.refresh calls build() before deleting the download): every
road that says its limit, cut into segments and filed by 0.01° square in
`speed-limits.db` (SQLite, the state dir). A road with no `maxspeed` has no
limit here: guessing from the kind of road would be wrong too often.

`GET /map/speed_limit?near=lat,lon[&heading=deg]` → `{kmh, road, distance_m}`
for the road nearest the point within NEAR_M, the one running the way of
`heading` first; `{kmh: null}` when none is that near.

Building reads the whole extract once with node locations kept on disk
(pyosmium's sparse_file_array): a country's nodes do not fit in a small
host's memory.
"""

from __future__ import annotations

import math
import os
import re
import sqlite3
import time
from pathlib import Path

#: Roads a car is on; service roads and tracks rarely say their limit.
ROADS = {"motorway", "trunk", "primary", "secondary", "tertiary", "unclassified", "residential",
         "motorway_link", "trunk_link", "primary_link", "secondary_link", "tertiary_link",
         "living_street", "road"}
#: Australia's and New Zealand's implied limits, as OSM writes them.
IMPLIED = {"au:urban": 50, "au:rural": 100, "au:living_street": 10, "nz:urban": 50, "nz:rural": 100}
#: A point this far from every road has no limit.
NEAR_M = 30.0
CELL = 0.01


def _db_path() -> Path:
    base = os.environ.get("XDG_STATE_HOME")
    d = (Path(base) if base else Path.home() / ".local" / "state") / "agent-media"
    d.mkdir(parents=True, exist_ok=True)
    return d / "speed-limits.db"


def kmh_of(raw: str | None) -> int | None:
    """`maxspeed` as km/h: "60", "60 km/h", "40 mph", "AU:urban"; None otherwise."""
    v = (raw or "").strip().lower()
    if not v:
        return None
    if v in IMPLIED:
        return IMPLIED[v]
    m = re.fullmatch(r"(\d{1,3})(?:\s*(km/h|kmh|kph|mph))?", v)
    if not m:
        return None
    n = int(m.group(1))
    if m.group(2) == "mph":
        n = round(n * 1.609)
    return n if 5 <= n <= 160 else None


def _cell(lat: float, lon: float) -> int:
    return math.floor(lat / CELL) * 100000 + math.floor(lon / CELL)


def build(pbf: str, out: Path | None = None) -> dict:
    """Read the extract, keep every road segment with a limit. Returns counts."""
    import osmium

    out = out or _db_path()
    part = out.with_suffix(".tmp")
    part.unlink(missing_ok=True)
    idx = Path(pbf).with_suffix(".nodes")
    db = sqlite3.connect(part)
    db.execute("CREATE TABLE seg (cell INTEGER, kmh INTEGER, road TEXT, oneway INTEGER,"
               " lat1 REAL, lon1 REAL, lat2 REAL, lon2 REAL)")
    ways = segs = 0
    rows: list[tuple] = []
    try:
        fp = (osmium.FileProcessor(pbf, osmium.osm.NODE | osmium.osm.WAY)
              .with_locations(f"sparse_file_array,{idx}"))
        for o in fp:
            if not o.is_way():
                continue
            tags = o.tags
            if tags.get("highway") not in ROADS:
                continue
            kmh = kmh_of(tags.get("maxspeed"))
            if kmh is None:
                continue
            road = tags.get("name") or tags.get("ref") or ""
            oneway = 1 if tags.get("oneway") in ("yes", "1", "true") or tags.get("highway") == "motorway" else 0
            pts = [(n.location.lat, n.location.lon) for n in o.nodes if n.location.valid()]
            if len(pts) < 2:
                continue
            ways += 1
            for (a, b), (c, d) in zip(pts, pts[1:]):
                for cell in {_cell(a, b), _cell(c, d)}:
                    rows.append((cell, kmh, road, oneway, a, b, c, d))
                segs += 1
            if len(rows) >= 50_000:
                db.executemany("INSERT INTO seg VALUES (?,?,?,?,?,?,?,?)", rows)
                rows.clear()
        if rows:
            db.executemany("INSERT INTO seg VALUES (?,?,?,?,?,?,?,?)", rows)
        db.execute("CREATE INDEX seg_cell ON seg (cell)")
        db.execute("CREATE TABLE meta (k TEXT PRIMARY KEY, v TEXT)")
        db.execute("INSERT INTO meta VALUES ('at', ?)", (str(time.time()),))
        db.commit()
    finally:
        db.close()
        idx.unlink(missing_ok=True)
    os.replace(part, out)
    return {"roads": ways, "segments": segs}


def _bearing(lat1, lon1, lat2, lon2) -> float:
    p1, p2, dl = math.radians(lat1), math.radians(lat2), math.radians(lon2 - lon1)
    y = math.sin(dl) * math.cos(p2)
    x = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return (math.degrees(math.atan2(y, x)) + 360) % 360


def _to_segment_m(lat, lon, a, b, c, d) -> float:
    kx = math.cos(math.radians(lat)) * 111_320.0
    ky = 110_540.0
    ax, ay = (b - lon) * kx, (a - lat) * ky
    bx, by = (d - lon) * kx, (c - lat) * ky
    dx, dy = bx - ax, by - ay
    ln = dx * dx + dy * dy
    t = 0.0 if ln == 0 else max(0.0, min(1.0, -(ax * dx + ay * dy) / ln))
    return math.hypot(ax + t * dx, ay + t * dy)


def nearest(lat: float, lon: float, heading: float | None = None) -> dict:
    """The limit of the road at a point (see the module's note)."""
    path = _db_path()
    if not path.exists():
        return {"kmh": None, "missing": True}
    cells = [_cell(lat + dy * CELL, lon + dx * CELL) for dy in (-1, 0, 1) for dx in (-1, 0, 1)]
    db = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        rows = db.execute(f"SELECT kmh, road, oneway, lat1, lon1, lat2, lon2 FROM seg WHERE cell IN "
                          f"({','.join('?' * len(cells))})", cells).fetchall()
    finally:
        db.close()
    best = None
    for kmh, road, oneway, a, b, c, d in rows:
        dist = _to_segment_m(lat, lon, a, b, c, d)
        if dist > NEAR_M:
            continue
        score = dist
        if heading is not None:
            diff = abs((_bearing(a, b, c, d) - heading + 180) % 360 - 180)
            # A two-way road runs both ways; a one-way only its own.
            off = diff if oneway else min(diff, 180 - diff)
            score += 0 if off <= 30 else 15 if off <= 60 else 40
        if best is None or score < best[0]:
            best = (score, kmh, road, dist)
    if best is None:
        return {"kmh": None}
    return {"kmh": best[1], "road": best[2], "distance_m": round(best[3], 1)}
