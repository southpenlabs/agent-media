"""Sharing a trip: a link someone else opens to see the phone's position and
arrival time live while it follows directions (David, 19 Nov 2026: "let's
actually add a whole lot of features based on Google maps"; the link "by
default ends when the trip ends but has an option for other" lengths, and
"anyone with the link" opens it).

`POST /map/trips {to: {name, lat, lon}, until?, mode?, eta_at?, at?: {lat, lon}}`
(signed in, behind the map's gate) starts one → `{trip: {id, token, until,
expires_at}}`. `until`: `trip` (default: gone when the trip ends), `15m`
(fifteen minutes after it ends, so they see the arrival), `1h` (an hour
after it starts, however it goes), `stop` (only when stopped by hand).
Every trip is gone `MAX_S` after it starts whatever `until` says.

`POST /map/trips/update {id, at: {lat, lon}, eta_at?, left_m?}` (the same
device) moves it on; `POST /map/trips/end {id, arrived?}` ends the trip
(with `until: trip` the link goes at once; `15m` keeps it that long;
`stop` and `1h` end the sharing itself only by `{id, stop: true}`).

`GET /map/trip?token=…` is public — no bearer, the token is the
credential (24 random bytes, URL-safe) → `{to, at?, at_time?, eta_at?,
left_m?, mode?, state: going|arrived|ended, started_at}`; 404 `no_trip`
once it is gone or never was. Nothing else of the person is in it.

Kept in `state_dir()/map-trips.json`, pruned of the gone on each write.
"""
from __future__ import annotations

import json
import os
import secrets
import threading
import time
from pathlib import Path

#: However `until` reads, a shared trip is gone this long after it starts.
MAX_S = 24 * 3600.0
UNTIL = {"trip", "15m", "1h", "stop"}
_LOCK = threading.Lock()


def _path() -> Path:
    from agent_media_core._paths import state_dir

    return state_dir() / "map-trips.json"


def _load(path: Path | None) -> dict[str, dict]:
    try:
        got = json.loads((path or _path()).read_text())
    except (OSError, ValueError):
        return {}
    return got if isinstance(got, dict) else {}


def _save(trips: dict[str, dict], path: Path | None, now: float) -> None:
    live = {k: v for k, v in trips.items() if _expires(v) > now}
    p = path or _path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(live))
    os.replace(tmp, p)


def _expires(t: dict) -> float:
    """When the link stops working."""
    start = float(t.get("started_at") or 0)
    cap = start + MAX_S
    if t.get("stopped_at"):
        return min(cap, float(t["stopped_at"]))
    until = t.get("until")
    ended = t.get("ended_at")
    if until == "1h":
        return min(cap, start + 3600)
    if until == "trip" and ended:
        return min(cap, float(ended))
    if until == "15m" and ended:
        return min(cap, float(ended) + 900)
    return cap


def _point(v) -> dict | None:
    if not isinstance(v, dict):
        return None
    try:
        lat, lon = float(v.get("lat")), float(v.get("lon"))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return None
    return {"lat": round(lat, 6), "lon": round(lon, 6)}


def _bad(code: str, error: str, status: int = 400) -> tuple[bool, dict]:
    return False, {"status": status, "code": code, "error": error}


def _num(v) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f and abs(f) != float("inf") else None


def start(owner: str, body: dict, path: Path | None = None, now: float | None = None) -> tuple[bool, dict]:
    now = time.time() if now is None else now
    to = body.get("to") if isinstance(body.get("to"), dict) else {}
    dest = _point(to)
    if not dest:
        return _bad("bad_point", "to is {name, lat, lon}")
    until = str(body.get("until") or "trip")
    if until not in UNTIL:
        return _bad("bad_until", "until is trip, 15m, 1h or stop")
    trip = {"id": f"trip-{secrets.token_hex(6)}", "token": secrets.token_urlsafe(24), "owner": owner,
            "until": until, "started_at": round(now, 3),
            "to": {"name": str(to.get("name") or "")[:200], **dest}}
    mode = str(body.get("mode") or "")
    if mode in ("walk", "drive", "bike", "transit"):
        trip["mode"] = mode
    _move(trip, body, now)
    with _LOCK:
        trips = _load(path)
        trips[trip["id"]] = trip
        _save(trips, path, now)
    return True, {"trip": {"id": trip["id"], "token": trip["token"], "until": until, "expires_at": _expires(trip)}}


def _move(trip: dict, body: dict, now: float) -> None:
    at = _point(body.get("at"))
    if at:
        trip["at"] = at
        trip["at_time"] = round(now, 3)
    eta = _num(body.get("eta_at"))
    if eta is not None:
        trip["eta_at"] = round(eta, 3)
    left = _num(body.get("left_m"))
    if left is not None:
        trip["left_m"] = max(0, round(left))


def _mine(trips: dict, owner: str, tid: str) -> dict | None:
    t = trips.get(tid)
    return t if t and t.get("owner") == owner else None


def update(owner: str, body: dict, path: Path | None = None, now: float | None = None) -> tuple[bool, dict]:
    now = time.time() if now is None else now
    with _LOCK:
        trips = _load(path)
        t = _mine(trips, owner, str(body.get("id") or ""))
        if not t or _expires(t) <= now:
            return _bad("no_trip", "no such shared trip", 404)
        _move(t, body, now)
        _save(trips, path, now)
    return True, {"expires_at": _expires(t)}


def end(owner: str, body: dict, path: Path | None = None, now: float | None = None) -> tuple[bool, dict]:
    now = time.time() if now is None else now
    with _LOCK:
        trips = _load(path)
        t = _mine(trips, owner, str(body.get("id") or ""))
        if not t:
            return _bad("no_trip", "no such shared trip", 404)
        if body.get("stop"):
            t["stopped_at"] = round(now, 3)
        if not t.get("ended_at"):
            t["ended_at"] = round(now, 3)
            if body.get("arrived"):
                t["arrived"] = True
        _save(trips, path, now)
    return True, {"expires_at": _expires(t), "gone": _expires(t) <= now}


def public(token: str, path: Path | None = None, now: float | None = None) -> tuple[bool, dict]:
    """`GET /map/trip?token=`: what the person with the link sees."""
    now = time.time() if now is None else now
    if not token:
        return _bad("no_trip", "no such shared trip", 404)
    for t in _load(path).values():
        if secrets.compare_digest(str(t.get("token") or ""), token) and _expires(t) > now:
            state = "arrived" if t.get("arrived") else "ended" if t.get("ended_at") else "going"
            out = {"to": t["to"], "state": state, "started_at": t["started_at"], "until": t["until"]}
            for k in ("at", "at_time", "eta_at", "left_m", "mode"):
                if k in t:
                    out[k] = t[k]
            return True, out
    return _bad("no_trip", "no such shared trip", 404)
