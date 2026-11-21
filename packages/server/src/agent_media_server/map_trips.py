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

Told in a chat (David, 22 Nov 2026, maps step 7 item 3 and step 10's
"Share a trip ... to someone in Messages (Matrix), a heads-up as you near;
ends when you arrive"): `POST /map/trips/send {id, thread}` sends the link
to a Matrix room as the owner ("David is on the way to Brunetti, arriving
about 3:40 pm. Follow along: <link>"), through the room's own send path;
from then on that room also gets a heads-up once the ETA is within
`NEAR_S` ("David is about 5 minutes from Brunetti.") and "David has arrived
at Brunetti." when the trip ends arrived. The name is the owner's own
display name in that room. The link is the server's public address
(MEDIA_PUBLIC_URL) `/trip/<token>`, which forwards to the app's page.

A trip that is ended by the trip (`until` trip or 15m) is also gone an hour
after its last ETA (`AFTER_ETA_S`): a phone that stops sending (flat, out
of signal, the app killed) never leaves a live link behind. `line`, the
route still ahead ([[lon, lat], …], at most `LINE_MAX` points kept), may
come with start and update, and the public answer carries it and the
person's name.

Kept in `state_dir()/map-trips.json`, pruned of the gone on each write.
"""
from __future__ import annotations

import json
import logging
import os
import secrets
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

#: However `until` reads, a shared trip is gone this long after it starts.
MAX_S = 24 * 3600.0
UNTIL = {"trip", "15m", "1h", "stop"}
#: A trip that ends with the trip is gone this long after its last ETA, whatever else.
AFTER_ETA_S = 3600.0
#: The heads-up to the chats it was sent to, once the ETA is this close.
NEAR_S = 300.0
#: Points of the route line kept for the public page.
LINE_MAX = 400
#: Chats one trip is told in.
TELL_MAX = 10
_LOCK = threading.Lock()
log = logging.getLogger("agent-media.server.map_trips")


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
    if until in ("trip", "15m") and t.get("eta_at"):
        # Never left live by a phone that stopped sending.
        cap = min(cap, float(t["eta_at"]) + AFTER_ETA_S)
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
    # Rounded once, as it is kept: a time kept rounded up must not outlive now (agent-media 1205a0a1's flake).
    now = round(time.time() if now is None else now, 3)
    to: dict = body["to"] if isinstance(body.get("to"), dict) else {}
    dest = _point(to)
    if not dest:
        return _bad("bad_point", "to is {name, lat, lon}")
    until = str(body.get("until") or "trip")
    if until not in UNTIL:
        return _bad("bad_until", "until is trip, 15m, 1h or stop")
    trip: dict[str, Any] = {"id": f"trip-{secrets.token_hex(6)}", "token": secrets.token_urlsafe(24), "owner": owner,
            "until": until, "started_at": round(now, 3),
            "to": {"name": str(to.get("name") or "")[:200], **dest}}
    name = str(body.get("name") or "").strip()[:80]
    if name:
        trip["name"] = name
    tz = str(body.get("tz") or "")
    if tz and _zone(tz) is not None:
        trip["tz"] = tz
    mode = str(body.get("mode") or "")
    if mode in ("walk", "drive", "bike", "transit"):
        trip["mode"] = mode
    _move(trip, body, now)
    with _LOCK:
        trips = _load(path)
        trips[trip["id"]] = trip
        _save(trips, path, now)
    out = {"id": trip["id"], "token": trip["token"], "until": until, "expires_at": _expires(trip)}
    url = link(trip["token"])
    if url:
        out["url"] = url
    return True, {"trip": out}


def link(token: str, base: str | None = None) -> str:
    """The public link: the server's public address `/trip/<token>`; "" with none set."""
    if base is None:
        from . import server_address

        base = server_address.current()
    return f"{base.rstrip('/')}/trip/{token}" if base else ""


def _line(v) -> list[list[float]] | None:
    """[[lon, lat], …] thinned to at most LINE_MAX points (first and last kept)."""
    if not isinstance(v, list):
        return None
    pts = []
    for p in v:
        if isinstance(p, (list, tuple)) and len(p) >= 2:
            q = _point({"lon": p[0], "lat": p[1]})
            if q:
                pts.append([q["lon"], q["lat"]])
    if len(pts) > LINE_MAX:
        step = (len(pts) - 1) / (LINE_MAX - 1)
        pts = [pts[round(i * step)] for i in range(LINE_MAX)]
    return pts


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
    line = _line(body.get("line"))
    if line is not None:
        trip["line"] = line


def _zone(tz: str):
    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo(tz)
    except Exception:  # noqa: BLE001 - an unknown zone is the server's own
        return None


def _clock(trip: dict, at: float) -> str:
    """`at` as the trip's local time, "3:40 pm"."""
    import datetime as _dt

    zone = _zone(trip.get("tz") or "") if trip.get("tz") else None
    d = _dt.datetime.fromtimestamp(at, zone) if zone else _dt.datetime.fromtimestamp(at)
    return f"{d.hour % 12 or 12}:{d.minute:02d} {'am' if d.hour < 12 else 'pm'}"


def words(kind: str, trip: dict, now: float, url: str = "") -> str:
    """What a chat is told: `start` (with the link), `near` or `arrived`.
    In the third person by the owner's name ("David is …"), else the first."""
    name = trip.get("name") or ""
    dest = (trip.get("to") or {}).get("name") or "the destination"
    eta = trip.get("eta_at")
    if kind == "start":
        when = f", arriving about {_clock(trip, float(eta))}" if eta else ""
        head = f"{name} is on the way to {dest}" if name else f"On my way to {dest}"
        return f"{head}{when}. Follow along: {url}" if url else f"{head}{when}."
    if kind == "near":
        mins = max(1, round((float(eta or now) - now) / 60))
        span = f"about {mins} minute{'s' if mins != 1 else ''}"
        return f"{name} is {span} from {dest}." if name else f"About {mins} minute{'s' if mins != 1 else ''} away from {dest}."
    return f"{name} has arrived at {dest}." if name else f"Arrived at {dest}."


def _tell_soon(threads: list[str], text: str) -> None:
    """Each chat told, off the request's thread; a failure logged, never raised."""
    def run() -> None:
        _tell(threads, text)

    threading.Thread(target=run, name="map-trip-tell", daemon=True).start()


def _tell(threads: list[str], text: str, rooms: Callable | None = None) -> list[tuple[str, bool, dict]]:
    from agent_media_core import failures

    if rooms is None:
        from . import matrix

        rooms = matrix.room
    out = []
    for th in threads:
        try:
            room = rooms(th)
            ok, detail = (room.send(text) if room is not None
                          else (False, {"status": 404, "code": "no_room", "error": "not a Matrix room"}))
        except Exception as e:  # noqa: BLE001 - one chat's failure leaves the others told
            failures.swallowed(e)
            ok, detail = False, {"error": e.__class__.__name__}
        if not ok:
            log.warning("shared trip: %s not told: %s", th[:8], detail.get("error"))
        out.append((th, ok, detail))
    return out


def _mine(trips: dict, owner: str, tid: str) -> dict | None:
    t = trips.get(tid)
    return t if t and t.get("owner") == owner else None


def update(owner: str, body: dict, path: Path | None = None, now: float | None = None,
           tell: Callable[[list[str], str], object] | None = None) -> tuple[bool, dict]:
    # Rounded once, as it is kept: a time kept rounded up must not outlive now (agent-media 1205a0a1's flake).
    now = round(time.time() if now is None else now, 3)
    with _LOCK:
        trips = _load(path)
        t = _mine(trips, owner, str(body.get("id") or ""))
        if not t or _expires(t) <= now:
            return _bad("no_trip", "no such shared trip", 404)
        _move(t, body, now)
        near = bool(t.get("tell")) and not t.get("near_told") and t.get("eta_at") is not None \
            and float(t["eta_at"]) - now <= NEAR_S
        if near:
            t["near_told"] = True
        _save(trips, path, now)
    if near:
        (tell or _tell_soon)(list(t["tell"]), words("near", t, now))
    return True, {"expires_at": _expires(t)}


def end(owner: str, body: dict, path: Path | None = None, now: float | None = None,
        tell: Callable[[list[str], str], object] | None = None) -> tuple[bool, dict]:
    # Rounded once, as it is kept: a time kept rounded up must not outlive now (agent-media 1205a0a1's flake).
    now = round(time.time() if now is None else now, 3)
    with _LOCK:
        trips = _load(path)
        t = _mine(trips, owner, str(body.get("id") or ""))
        if not t:
            return _bad("no_trip", "no such shared trip", 404)
        if body.get("stop"):
            t["stopped_at"] = round(now, 3)
        arrived = False
        if not t.get("ended_at"):
            t["ended_at"] = round(now, 3)
            if body.get("arrived"):
                t["arrived"] = arrived = True
        _save(trips, path, now)
    if arrived and t.get("tell"):
        (tell or _tell_soon)(list(t["tell"]), words("arrived", t, now))
    return True, {"expires_at": _expires(t), "gone": _expires(t) <= now}


def send(owner: str, body: dict, path: Path | None = None, now: float | None = None,
         rooms: Callable | None = None, base: str | None = None) -> tuple[bool, dict]:
    """`POST /map/trips/send {id, thread}`: the link sent to a Matrix room as
    the owner, and that room told of the arrival from then on."""
    now = round(time.time() if now is None else now, 3)
    tid, thread = str(body.get("id") or ""), str(body.get("thread") or "")
    if rooms is None:
        from . import matrix

        rooms = matrix.room
    with _LOCK:
        t = _mine(_load(path), owner, tid)
    if not t or _expires(t) <= now:
        return _bad("no_trip", "no such shared trip", 404)
    room = rooms(thread) if thread else None
    if room is None:
        return _bad("no_room", "thread is a Matrix room's", 404)
    url = link(t["token"], base)
    if not url:
        return _bad("no_public_address", "this server has no public address (MEDIA_PUBLIC_URL)", 409)
    if not t.get("name"):
        # The owner as that room knows them: what the person reading sees anyway.
        from agent_media_core import matrix as _m

        who = (getattr(room, "members", None) or {}).get(_m.owner_of(), "")
        if who and not who.startswith("@"):
            t["name"] = str(who)[:80]
    text = words("start", t, now, url)
    ok, detail = room.send(text)
    if not ok:
        return False, {"status": detail.get("status") or 502, "code": "not_sent",
                       "error": detail.get("error") or "the message was not sent"}
    with _LOCK:
        trips = _load(path)
        kept = _mine(trips, owner, tid)
        if kept:
            tells = [x for x in kept.get("tell") or [] if x != thread] + [thread]
            kept["tell"] = tells[-TELL_MAX:]
            if t.get("name"):
                kept["name"] = t["name"]
            if kept.get("eta_at") is not None and float(kept["eta_at"]) - now <= NEAR_S:
                kept["near_told"] = True  # close already: the first message is the heads-up
            _save(trips, path, now)
    return True, {"sent": True, "text": text, "url": url}


def public(token: str, path: Path | None = None, now: float | None = None) -> tuple[bool, dict]:
    """`GET /map/trip?token=`: what the person with the link sees."""
    # Rounded once, as it is kept: a time kept rounded up must not outlive now (agent-media 1205a0a1's flake).
    now = round(time.time() if now is None else now, 3)
    if not token:
        return _bad("no_trip", "no such shared trip", 404)
    for t in _load(path).values():
        if secrets.compare_digest(str(t.get("token") or ""), token) and _expires(t) > now:
            state = "arrived" if t.get("arrived") else "ended" if t.get("ended_at") else "going"
            out = {"to": t["to"], "state": state, "started_at": t["started_at"], "until": t["until"],
                   "expires_at": _expires(t)}
            for k in ("at", "at_time", "eta_at", "left_m", "mode", "name", "line"):
                if k in t:
                    out[k] = t[k]
            return True, out
    return _bad("no_trip", "no such shared trip", 404)
