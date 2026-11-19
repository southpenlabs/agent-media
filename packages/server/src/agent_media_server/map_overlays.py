"""Pictures laid over the Map: a venue's own map, lined up with the streets
(David, 19 Nov 2026: "Can we get an overlay with the Hot Springs map").

Each overlay is two files in `state_dir()/map-overlays/`: `<id>.json`
`{name, image: "<file>", coordinates: [[lon, lat] ×4], opacity?, credit?}`
— the picture's top-left, top-right, bottom-right and bottom-left corners,
MapLibre's image source order — and the picture itself (JPEG or PNG) beside
it. Nothing is fetched at run time: the owner puts them there (the first,
Peninsula Hot Springs' facility map, was fitted to OpenStreetMap by hand).

They are Map layers of kind `image` (`GET /map/layers`): `{id: "overlay:<id>",
name, kind: "image", default_on: false, image: {coordinates, opacity,
credit?}}`, no features. `GET /map/overlay?id=<id>` → `{id, data_url}`, the
picture as a data: URL, so the app needs no second credentialed fetch.
Behind the map's gate, as every layer is.
"""
from __future__ import annotations

import base64
import json
import re
from pathlib import Path

_ID = re.compile(r"[a-z0-9][a-z0-9-]{0,39}")
_TYPES = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp"}


def _dir() -> Path:
    from agent_media_core._paths import state_dir

    return state_dir() / "map-overlays"


def _read(oid: str, folder: Path | None = None) -> dict | None:
    if not _ID.fullmatch(oid):
        return None
    d = folder or _dir()
    try:
        meta = json.loads((d / f"{oid}.json").read_text())
    except (OSError, ValueError):
        return None
    coords = meta.get("coordinates")
    img = str(meta.get("image") or "")
    ok = (isinstance(coords, list) and len(coords) == 4
          and all(isinstance(c, list) and len(c) == 2 and all(isinstance(v, (int, float)) for v in c) for c in coords)
          and Path(img).name == img and Path(img).suffix.lower() in _TYPES and (d / img).is_file())
    return {**meta, "id": oid} if ok else None


def overlays(folder: Path | None = None) -> list[dict]:
    """Every overlay that reads whole, by id."""
    d = folder or _dir()
    out = []
    for f in sorted(d.glob("*.json")) if d.is_dir() else []:
        got = _read(f.stem, d)
        if got:
            out.append(got)
    return out


def layers(folder: Path | None = None) -> list[dict]:
    out = []
    for o in overlays(folder):
        image = {"coordinates": o["coordinates"], "opacity": float(o.get("opacity") or 0.85)}
        if o.get("credit"):
            image["credit"] = str(o["credit"])
        out.append({"id": f"overlay:{o['id']}", "name": str(o.get("name") or o["id"]), "kind": "image",
                    "default_on": False, "image": image})
    return out


def picture(oid: str, folder: Path | None = None) -> tuple[bool, dict]:
    """`GET /map/overlay?id=`: the picture as a data: URL."""
    o = _read(oid, folder)
    if not o:
        return False, {"status": 404, "code": "no_overlay", "error": "no such overlay"}
    f = (folder or _dir()) / o["image"]
    data = base64.b64encode(f.read_bytes()).decode()
    return True, {"id": oid, "data_url": f"data:{_TYPES[f.suffix.lower()]};base64,{data}"}
