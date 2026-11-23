"""Other states' official camera sites on the Map and in the camera alerts
(David, 23 Nov 2026: "Should we apply this to other state maps?" → other
states' camera lists).

What each state publishes was checked on 23 Nov 2026 (the maps proposal's
step 3 has the whole list). Only the lists that are open and give a point
for each site are here; Victoria's phone and seatbelt sites, a road and
suburb each, stay in map_vic.py:

- ACT (data.act.gov.au, CC BY 4.0, Access Canberra, updated monthly):
  "Traffic speed camera locations" (fixed, red light and speed, point to
  point, and the mobile speed vans' approved sites; decommissioned ones
  dated) and "Mobile Distraction - Fixed and Transportable camera locations"
  (the phone cameras' sites).
- NSW (opendata.transport.nsw.gov.au, CC BY, Transport for NSW): fixed and
  red-light speed cameras with their points. Last published in 2021, so the
  layer says it is old; NSW's mobile sites are roads and suburbs, not drawn.

Each state is its own setting, off unless switched on (`MEDIA_MAP_CAMERAS_ACT=1`,
`MEDIA_MAP_CAMERAS_NSW=1`): camera alerts are legal in Australia, but a server
shows the states its owner chose. `python -m agent_media_server.map_gov_cameras
refresh` (a weekly timer) reads each switched-on state's lists into
`gov-cameras-<state>.json` in the state dir; a list that fails keeps the last.
"""

from __future__ import annotations

import csv
import io
import json
import math
import os
import time
import urllib.request
from collections.abc import Callable
from pathlib import Path

from agent_media_core import failures as _failures

USER_AGENT = "agent-media map (https://github.com/southpenlabs/agent-media)"
ACT_SPEED = "https://www.data.act.gov.au/api/views/426s-vdu4/rows.csv?accessType=DOWNLOAD"
ACT_PHONE = "https://www.data.act.gov.au/api/views/5ezj-yjxz/rows.csv?accessType=DOWNLOAD"
_NSW = "https://opendata.transport.nsw.gov.au/data/dataset/fb34bd89-443a-448c-a4a5-7c8caab70c44/resource"
NSW_FIXED = f"{_NSW}/bcf2f6f4-ecfb-40e1-a807-0d5eb5f51507/download/fixed-speed-cameras_1.csv"
NSW_RED = f"{_NSW}/debd70a9-f9f4-471c-81ae-c84098576ea6/download/red-light-speed-cameras_1.csv"

#: How each kind is named on the map; `enforces` is what /map/alerts and the OSM layer call it.
TITLES = {"speed": "Speed camera", "red_light": "Red light and speed camera", "average_speed": "Point-to-point camera",
          "mobile_speed": "Mobile speed camera site", "phone": "Phone camera site"}
#: Kinds the phone alerts on as it drives: a mobile van's approved site is a place it may be, not a camera.
ALERT_KINDS = ("speed", "red_light", "average_speed", "phone")


def _rows(text: str) -> list[dict]:
    return [{(k or "").strip(): (v or "").strip() for k, v in r.items()}
            for r in csv.DictReader(io.StringIO(text.lstrip("﻿")))]


def _point(lat: str, lon: str) -> list[float] | None:
    try:
        la, lo = float(lat), float(lon)
    except ValueError:
        return None
    return [lo, la] if -45 <= la <= -9 and 110 <= lo <= 160 else None


def _feature(coords: list[float], kind: str, detail: str, **extra) -> dict:
    return {"type": "Feature", "geometry": {"type": "Point", "coordinates": coords},
            "properties": {"title": TITLES[kind], "detail": detail, "enforces": kind, **extra}}


def parse_act(texts: dict[str, str]) -> list[dict]:
    kinds = {"FIXED ONLY SPEED CAMERA": "speed", "RED LIGHT AND SPEED CAMERA": "red_light",
             "POINT TO POINT CAMERA": "average_speed", "MOBILE SPEED CAMERA": "mobile_speed"}
    out = []
    for r in _rows(texts.get(ACT_SPEED, "")):
        kind = kinds.get(r.get("CAMERA TYPE", "").upper())
        p = _point(r.get("LATITUDE", ""), r.get("LONGITUDE", ""))
        if not kind or not p or r.get("Decommissioned Camera_Date"):
            continue
        out.append(_feature(p, kind, r.get("LOCATION DESCRIPTION", "").title()))
    for r in _rows(texts.get(ACT_PHONE, "")):
        p = _point(r.get("Latitude", ""), r.get("Longitude", ""))
        if not p:
            continue
        way = r.get("Direction of travel to be monitored", "")
        out.append(_feature(p, "phone", r.get("Site Name", "") + (f" (facing traffic going {way})" if way else ""),
                            site=r.get("Site Type", "")))
    return out


def parse_nsw(texts: dict[str, str]) -> list[dict]:
    out = []
    for url, kind in ((NSW_FIXED, "speed"), (NSW_RED, "red_light")):
        for r in _rows(texts.get(url, "")):
            where = ", ".join(x for x in (r.get("ROAD/S", ""), r.get("SUBURB/TOWN", "")) if x)
            if r.get("SZ?", "").upper() == "Y" and "school zone" not in where.lower():
                where += " (school zone)"
            for i in (1, 2, 3):
                p = _point(r.get(f"Lat({i})", ""), r.get(f"Long({i})", ""))
                if p:
                    out.append(_feature(p, kind, where))
    return out


#: state → its lists, parser, setting, credit and note.
STATES: dict[str, dict] = {
    "act": {"name": "ACT", "urls": (ACT_SPEED, ACT_PHONE), "parse": parse_act, "env": "MEDIA_MAP_CAMERAS_ACT",
            "source": "ACT Government (Access Canberra), CC BY 4.0",
            "note": "Official sites, updated monthly: fixed, red light, point-to-point and phone cameras, and the "
                    "mobile vans' approved sites (a van may be at any of them, or none)."},
    "nsw": {"name": "NSW", "urls": (NSW_FIXED, NSW_RED), "parse": parse_nsw, "env": "MEDIA_MAP_CAMERAS_NSW",
            "source": "Transport for NSW, CC BY",
            "note": "Fixed and red-light speed cameras as last published, in 2021: newer cameras are missing. "
                    "Mobile sites and phone cameras are not drawn."},
}


def enabled(state: str) -> bool:
    s = STATES.get(state)
    return s is not None and (os.environ.get(s["env"]) or "").lower() in ("1", "on", "yes", "true")


def layer_id(state: str) -> str:
    return f"cameras_{state}"


def layers() -> list[dict]:
    return [{"id": layer_id(k), "name": f"{s['name']} cameras (official)", "kind": "points", "icon": "camera",
             "colour": "#d9a400", "default_on": False, "bbox": True, "min_zoom": 9, "source": s["source"],
             "note": s["note"]} for k, s in STATES.items() if enabled(k)]


def _path(state: str) -> Path:
    base = os.environ.get("XDG_STATE_HOME")
    d = (Path(base) if base else Path.home() / ".local" / "state") / "agent-media"
    d.mkdir(parents=True, exist_ok=True)
    return d / f"gov-cameras-{state}.json"


def _get(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=60) as res:
        return res.read().decode("utf-8-sig", "replace")


def refresh(state: str, get: Callable[[str], str] | None = None) -> dict:
    """One state's lists read and kept; answers how many sites."""
    s = STATES[state]
    texts = {u: (get or _get)(u) for u in s["urls"]}
    feats = s["parse"](texts)
    if not feats:
        raise ValueError(f"{state}: no camera sites read")
    path = _path(state)
    part = path.with_suffix(".tmp")
    part.write_text(json.dumps({"at": time.time(), "features": feats}))
    os.replace(part, path)
    return {"state": state, "sites": len(feats)}


def _kept(state: str) -> dict | None:
    try:
        return json.loads(_path(state).read_text())
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as e:
        _failures.swallowed(e)
        return None


def read(state: str, bbox) -> tuple[list[dict], dict]:
    if bbox is None:
        return [], {"zoom_in": True}
    kept = _kept(state)
    if kept is None:
        return [], {"missing": True}
    w, s, e, n = bbox
    feats = [f for f in kept.get("features") or []
             if w <= f["geometry"]["coordinates"][0] <= e and s <= f["geometry"]["coordinates"][1] <= n]
    return feats, {"partial": False, "as_of": round(kept.get("at") or 0)}


def alert_points(box, near: list[dict]) -> list[dict]:
    """The switched-on states' cameras in the box as /map/alerts points, leaving out
    one within 40 m of a point already there of the same kind (OSM often has it too)."""
    out: list[dict] = []
    for state in STATES:
        if not enabled(state):
            continue
        feats, _x = read(state, box)
        for f in feats:
            kind = f["properties"].get("enforces")
            if kind not in ALERT_KINDS:
                continue
            lon, lat = f["geometry"]["coordinates"][:2]
            if any(p.get("kind") == kind and _metres(lat, lon, p["lat"], p["lon"]) < 40 for p in near + out):
                continue
            out.append({"lat": lat, "lon": lon, "kind": kind, "source": state})
    return out


def _metres(a_lat: float, a_lon: float, b_lat: float, b_lon: float) -> float:
    dy = (a_lat - b_lat) * 111_320
    dx = (a_lon - b_lon) * 111_320 * math.cos(math.radians(a_lat))
    return math.hypot(dx, dy)


def main(argv: list[str] | None = None) -> int:
    import sys

    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] != ["refresh"]:
        print("usage: python -m agent_media_server.map_gov_cameras refresh [state …]", file=sys.stderr)
        return 2
    from agent_media_core import logs
    from agent_media_core.intake._env import load_env_file

    load_env_file("map-gov-cameras")
    logs.setup()
    want = argv[1:] or [k for k in STATES if enabled(k)]
    rc = 0
    for state in want:
        try:
            print(json.dumps(refresh(state)), flush=True)
        except (OSError, ValueError, KeyError) as e:
            print(f"{state} cameras not refreshed: {e}", file=sys.stderr, flush=True)
            rc = 1
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
