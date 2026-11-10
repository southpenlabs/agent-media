"""People's reports of where phone and seatbelt cameras stand, on the Map and
in the camera alerts (David, 10 Nov 2026: "let's use the speed camera
locations data for personal use").

A personal-use method (`camera-reports`, agent_media_core.personal_use): an
add-on provides it, and only an install serving its owner turns it on. The
add-on knows where the reports are and how to read them; this module keeps
them and serves them. The add-on's object has:

- `URLS`: the addresses to read, each fetched through the phone (the site
  refuses a cloud host; §6.21 `fetch`);
- `parse(texts: dict[url, str]) -> list[dict]`: each report as
  `{lat, lon, name, kind ("phone" | "suspected" | ...), video?, facing?}`;
- `SOURCE`: the credit shown with the layer.

`python -m agent_media_server.map_reports refresh` (a timer every few hours:
it does nothing when the kept file is under REFRESH_S old, and a phone that
does not answer is asked again next time) keeps `camera-reports.json` in the
state dir.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

LAYER_ID = "camera_reports"
#: The kept reports are read again once this old.
REFRESH_S = 20 * 3600


def _provider():
    from agent_media_core import personal_use

    return personal_use.provider("camera-reports") if personal_use.allowed("camera-reports") else None


def _state() -> Path:
    base = os.environ.get("XDG_STATE_HOME")
    d = (Path(base) if base else Path.home() / ".local" / "state") / "agent-media"
    d.mkdir(parents=True, exist_ok=True)
    return d / "camera-reports.json"


def layers() -> list[dict]:
    p = _provider()
    if p is None:
        return []
    return [{"id": LAYER_ID, "name": "Reported phone cameras", "kind": "points", "icon": "camera",
             "colour": "#c0392b", "default_on": False, "bbox": True, "min_zoom": 9,
             "source": getattr(p, "SOURCE", "community reports"),
             "note": "Where people have reported phone and seatbelt cameras: confirmed spots, and "
                     "suspected ones where several reports came in over a few days."}]


def _kept() -> dict:
    try:
        return json.loads(_state().read_text())
    except (OSError, ValueError):
        return {}


def reports() -> list[dict]:
    """The kept reports (none where the method is off)."""
    if _provider() is None:
        return []
    return list(_kept().get("reports") or [])


def read(bbox) -> tuple[list[dict], dict]:
    if bbox is None:
        return [], {"zoom_in": True}
    kept = _kept()
    if not kept:
        return [], {"missing": True}
    w, s, e, n = bbox
    out = []
    for r in kept.get("reports") or []:
        if not (s <= r["lat"] <= n and w <= r["lon"] <= e):
            continue
        title = "Phone camera (reported)" if r.get("kind") == "phone" else "Suspected phone camera"
        props = {"title": title, "detail": r.get("name") or ""}
        if r.get("video"):
            props["link"] = f"https://www.youtube.com/watch?v={r['video']}"
        out.append({"type": "Feature", "properties": props,
                    "geometry": {"type": "Point", "coordinates": [r["lon"], r["lat"]]}})
    return out, {"partial": False, "as_of": round(kept.get("at") or 0)}


def refresh(force: bool = False) -> dict:
    """Read the add-on's addresses through the phone and keep the reports."""
    p = _provider()
    if p is None:
        raise SystemExit("camera-reports is not allowed here (personal use, and the add-on)")
    kept = _kept()
    if not force and time.time() - (kept.get("at") or 0) < REFRESH_S:
        return {"reports": len(kept.get("reports") or []), "fresh": True}
    from agent_media_core import phone_ask

    texts = {}
    for url in p.URLS:
        got = phone_ask.ask("fetch", "Read the camera reports for the Map and the camera alerts",
                            timeout_s=110, url=url)
        res = got.get("result") or {}
        if got.get("status") != "ok" or res.get("status") != 200:
            return {"reports": len(kept.get("reports") or []), "asked": got.get("status"),
                    "http": res.get("status")}
        texts[url] = Path(res["path"]).read_text(encoding="utf-8", errors="replace")
    rows = [r for r in p.parse(texts)
            if isinstance(r, dict) and -90 <= float(r.get("lat", 99)) <= 90 and -180 <= float(r.get("lon", 999)) <= 180]
    out = {"at": time.time(), "reports": rows}
    part = _state().with_suffix(".tmp")
    part.write_text(json.dumps(out))
    os.replace(part, _state())
    return {"reports": len(rows)}


if __name__ == "__main__":
    import sys

    if sys.argv[1:2] != ["refresh"]:
        raise SystemExit("usage: python -m agent_media_server.map_reports refresh [--force]")
    from agent_media_core.intake._env import load_env_file

    load_env_file("map-reports")
    print(json.dumps(refresh(force="--force" in sys.argv)))
