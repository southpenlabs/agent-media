"""The app's Map page: which layers there are, and each layer's features
(docs/proposals/2026-11-06-maps.md, step 1).

`GET /map/layers` lists the layers; `GET /map/layer?id=<id>[&bbox=w,s,e,n]`
answers one as a GeoJSON FeatureCollection. The app draws what it is sent
and knows nothing of where a layer comes from (the app stays generic).

Built in: `phone`, the phone's shared location (§6.21b), the freshest fix
per phone with its accuracy and age. More layers come from add-ons through
the `agent_media.map_layers` entry point: each names a callable returning
`[{"layer": {...}, "features": callable(bbox) -> [feature, ...]}]`, so a
region's cameras or a house's rooms live outside this repo.

Location is the owner's: a guest device is refused (403), as it may read
chats and listen but has no business knowing where the phone is.
"""

from __future__ import annotations

import logging
import time
from importlib.metadata import entry_points

from . import auth, phone

log = logging.getLogger(__name__)

ENTRY_GROUP = "agent_media.map_layers"

_PHONE = {"id": "phone", "name": "My phone", "kind": "points", "icon": "phone",
          "default_on": True, "refresh_s": 30, "private": True}


def _phone_features(_bbox) -> list[dict]:
    now = time.time()
    out = []
    for f in phone.shared_all():
        props = {"title": f.get("device") or "My phone", "at": f["at"],
                 "age_s": round(max(0.0, now - f["at"])), "precise": f["precise"]}
        if "accuracy_m" in f:
            props["accuracy_m"] = f["accuracy_m"]
        out.append({"type": "Feature", "properties": props,
                    "geometry": {"type": "Point", "coordinates": [f["lon"], f["lat"]]}})
    return out


def _addons() -> list[dict]:
    found = []
    for ep in entry_points(group=ENTRY_GROUP):
        try:
            for item in ep.load()() or []:
                lay = item.get("layer") or {}
                if lay.get("id") and callable(item.get("features")):
                    found.append(item)
        except Exception:  # an add-on's fault must not take the map down
            log.exception("map layer add-on %s failed", ep.name)
    return found


def _all() -> list[dict]:
    seen = {"phone"}
    out = [{"layer": _PHONE, "features": _phone_features}]
    for item in _addons():
        if item["layer"]["id"] not in seen:
            seen.add(item["layer"]["id"])
            out.append(item)
    return out


def _gate(bearer: str) -> tuple[bool, dict]:
    user, err = auth.gate(bearer)
    if not user:
        return False, err
    if auth.is_guest(bearer):
        return False, {"status": 403, "code": "guest",
                       "error": "a guest device does not see the map"}
    return True, {}


def _bbox(raw: str):
    """`w,s,e,n` as four floats, or None when absent or not that."""
    if not raw:
        return None
    try:
        w, s, e, n = (float(x) for x in raw.split(","))
    except ValueError:
        return None
    if not (-180 <= w <= 180 and -180 <= e <= 180 and -90 <= s <= n <= 90):
        return None
    return (w, s, e, n)


def _inside(feature: dict, bbox) -> bool:
    geom = feature.get("geometry") or {}
    if bbox is None or geom.get("type") != "Point":
        return True
    lon, lat = geom["coordinates"][:2]
    w, s, e, n = bbox
    in_lon = w <= lon <= e if w <= e else (lon >= w or lon <= e)  # across 180
    return in_lon and s <= lat <= n


def layers(bearer: str) -> tuple[bool, dict]:
    ok, err = _gate(bearer)
    if not ok:
        return False, err
    return True, {"layers": [dict(i["layer"]) for i in _all()]}


def layer(layer_id: str, bbox_raw: str, bearer: str) -> tuple[bool, dict]:
    ok, err = _gate(bearer)
    if not ok:
        return False, err
    item = next((i for i in _all() if i["layer"]["id"] == layer_id), None)
    if item is None:
        return False, {"status": 404, "error": "no such layer"}
    bbox = _bbox(bbox_raw)
    try:
        feats = [f for f in item["features"](bbox) or [] if _inside(f, bbox)]
    except Exception:
        log.exception("map layer %s failed", layer_id)
        return False, {"status": 502, "error": "that layer could not be read"}
    return True, {"type": "FeatureCollection", "layer": layer_id,
                  "at": round(time.time(), 3), "features": feats}
