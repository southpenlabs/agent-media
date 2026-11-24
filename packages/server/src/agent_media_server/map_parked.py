"""Where I parked (maps proposal step 10; docs/proposals/2026-11-06-maps.md).

David, 24 Nov 2026, choosing it second: "the car's spot kept when the phone leaves the
car's Bluetooth, and directions back to it". The phone (CarPark.java) takes one fix as it
disconnects from a car stereo and posts it here; reconnecting takes it off, the car being
on the move again. With no car Bluetooth, the Map's "Parked here" posts the device's own
position, and an agent can (`media place parked --here`).

Kept per account in `state_dir()/map-parked.json` (`{account: spot}`, 0600): one spot,
the newest. Nothing else is kept of where the phone has been.

    GET  /map/parked → {parked: {lat, lon, accuracy_m?, at, how, car?, age_s} | null}
    POST /map/parked {lat, lon, accuracy_m?, at?, how?: bluetooth|hand, car?} → {parked}
         {clear: true, how?: bluetooth} → {cleared: bool}
         the owner's devices (not a guest) and the host's token

A Bluetooth clear only takes off a spot Bluetooth set (a spot set by hand stays until
the next park). The Map's `parked` layer draws it ("Your car", how long ago, Directions).
400 codes: bad_point, bad_at.
"""
from __future__ import annotations

import json
import math
import os
import threading
import time
from pathlib import Path

#: A spot older than this is not shown (a car is not parked for a month).
MAX_AGE_S = 30 * 86400
HOWS = ("bluetooth", "hand")
_LOCK = threading.Lock()

LAYER = {"id": "parked", "name": "Your car", "kind": "points", "icon": "car",
         "default_on": True, "refresh_s": 60, "private": True}


def _path() -> Path:
    from agent_media_core._paths import state_dir

    return state_dir() / "map-parked.json"


def _load(path: Path | None) -> dict:
    try:
        got = json.loads((path or _path()).read_text())
    except (OSError, ValueError):
        return {}
    return got if isinstance(got, dict) else {}


def _save(book: dict, path: Path | None) -> None:
    p = path or _path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(book, ensure_ascii=False, indent=1))
    os.chmod(tmp, 0o600)
    os.replace(tmp, p)


def _num(v) -> float | None:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    return float(v) if math.isfinite(v) else None


def get(account: str, path: Path | None = None, now: float | None = None) -> tuple[bool, dict]:
    now = time.time() if now is None else now
    spot = _load(path).get(account)
    if not isinstance(spot, dict) or now - float(spot.get("at") or 0) > MAX_AGE_S:
        return True, {"parked": None}
    return True, {"parked": {**spot, "age_s": max(0, round(now - float(spot["at"])))}}


def change(account: str, body, path: Path | None = None, now: float | None = None) -> tuple[bool, dict]:
    if not isinstance(body, dict):
        return False, {"status": 400, "code": "bad_body", "error": "not JSON"}
    now = time.time() if now is None else now
    how = body.get("how") if body.get("how") in HOWS else "hand"
    with _LOCK:
        book = _load(path)
        if body.get("clear") is True:
            spot = book.get(account)
            if not isinstance(spot, dict) or (how == "bluetooth" and spot.get("how") != "bluetooth"):
                return True, {"cleared": False}
            book.pop(account, None)
            _save(book, path)
            return True, {"cleared": True}
        lat, lon = _num(body.get("lat")), _num(body.get("lon"))
        if lat is None or lon is None or not (-90 <= lat <= 90 and -180 <= lon <= 180):
            return False, {"status": 400, "code": "bad_point", "error": "lat and lon, in range"}
        at = _num(body.get("at"))
        at = now if at is None else at
        if at > now + 300 or at < now - 86400:
            return False, {"status": 400, "code": "bad_at", "error": "at is not recent"}
        spot: dict = {"lat": round(lat, 6), "lon": round(lon, 6), "at": at, "how": how}
        acc = _num(body.get("accuracy_m"))
        if acc is not None and acc >= 0:
            spot["accuracy_m"] = round(acc)
        car = " ".join(str(body.get("car") or "").split())[:60]
        if car:
            spot["car"] = car
        book[account] = spot
        _save(book, path)
    return True, {"parked": {**spot, "age_s": max(0, round(now - at))}}


def features(account: str, path: Path | None = None, now: float | None = None) -> list[dict]:
    """The `parked` layer: the car's spot, if there is one."""
    spot = get(account, path, now)[1]["parked"]
    if not spot:
        return []
    props: dict = {"title": "Your car", "icon": "car", "age_s": spot["age_s"], "how": spot["how"]}
    if spot.get("accuracy_m") is not None:
        props["accuracy_m"] = spot["accuracy_m"]
    if spot.get("car"):
        props["detail"] = spot["car"]
    return [{"type": "Feature", "properties": props,
             "geometry": {"type": "Point", "coordinates": [spot["lon"], spot["lat"]]}}]
