"""Rock pools: a heads-up the day before a good low tide at a spot the person watches (maps
proposal step 11, "Rock pools"; docs/proposals/2026-11-06-maps.md).

David, 22 Nov 2026: "when rock pools are exposed ... and any other relevant information";
the proposal's example, "Rock pools at Blairgowrie tomorrow: low 0.2 m at 10:40"; 24 Nov:
"Go on with the rock pool heads-up".

A spot is watched from its Outdoors section on the Map ("Tell me the day before") or by
an agent (`media place pools watch`). Each evening the scheduler's `rock-pools` job asks
`POST /map/pools/check`: for each watched spot, map_outdoors' answer there, and for each
`good` low tide (daylight, the week's lowest third) falling tomorrow in the spot's own
time, a note on the phone (§6.23, `pools-<id>`): "Rock pools at Blairgowrie tomorrow",
"Low −1.19 m at 5:40 pm.", with a warning when the swell then is BIG_SWELL_M or more.
Each spot is told once a day (kept in `told`), so a second run says nothing new.

    GET  /map/pools → {spots: [{id, name, lat, lon}]}       the owner's devices, the host
    POST /map/pools {name, lat, lon} → {spot}; {id, delete: true} → {deleted}
    POST /map/pools/check → {told: [{id, name, at, m, swell_m?}]}   the host's token only

The notes reach the phones the host's notes reach, so only the owner's spots (account
"") are checked; another account's watched spots are kept and shown, not yet told.
Kept in `state_dir()/map-pools.json`. 400 codes: bad_point, bad_name, too_many (20).
"""
from __future__ import annotations

import datetime as dt
import json
import math
import os
import secrets
import threading
import time
from pathlib import Path
from typing import Callable
from zoneinfo import ZoneInfo

from . import map_outdoors

MAX = 20
BIG_SWELL_M = map_outdoors.BIG_SWELL_M
_LOCK = threading.Lock()

Outdoors = Callable[[float, float], "tuple[bool, dict]"]
Post = Callable[[dict], object]


def _path() -> Path:
    from agent_media_core._paths import state_dir

    return state_dir() / "map-pools.json"


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


def listing(account: str, path: Path | None = None) -> tuple[bool, dict]:
    return True, {"spots": list(_load(path).get(account) or [])}


def change(account: str, body, path: Path | None = None) -> tuple[bool, dict]:
    if not isinstance(body, dict):
        return False, {"status": 400, "code": "bad_body", "error": "not JSON"}
    with _LOCK:
        book = _load(path)
        spots: list[dict] = list(book.get(account) or [])
        if body.get("delete") is True:
            sid = str(body.get("id") or "")
            if not any(s.get("id") == sid for s in spots):
                return False, {"status": 404, "code": "not_found", "error": "no such spot"}
            book[account] = [s for s in spots if s.get("id") != sid]
            _save(book, path)
            return True, {"deleted": sid}
        lat, lon = body.get("lat"), body.get("lon")
        if (isinstance(lat, bool) or isinstance(lon, bool) or not isinstance(lat, (int, float))
                or not isinstance(lon, (int, float)) or not math.isfinite(lat) or not math.isfinite(lon)
                or not (-90 <= lat <= 90 and -180 <= lon <= 180)):
            return False, {"status": 400, "code": "bad_point", "error": "lat and lon, in range"}
        name = " ".join(str(body.get("name") or "").split())[:80]
        if not name:
            return False, {"status": 400, "code": "bad_name", "error": "the spot's name"}
        # The same spot again (within ~100 m) is the one already watched, renamed.
        for s in spots:
            if map_outdoors_metres(s["lat"], s["lon"], lat, lon) < 100:
                s["name"] = name
                _save(book, path)
                return True, {"spot": s}
        if len(spots) >= MAX:
            return False, {"status": 400, "code": "too_many", "error": f"at most {MAX} spots"}
        spot = {"id": "rp-" + secrets.token_hex(4), "name": name, "lat": round(float(lat), 5), "lon": round(float(lon), 5)}
        spots.append(spot)
        book[account] = spots
        _save(book, path)
    return True, {"spot": spot}


def map_outdoors_metres(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = (math.sin((p2 - p1) / 2) ** 2
         + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2)
    return 2 * r * math.asin(math.sqrt(min(1.0, a)))


def _clock(t: float, tz: ZoneInfo) -> str:
    d = dt.datetime.fromtimestamp(t, tz)
    return f"{d.hour % 12 or 12}:{d.minute:02d} {'am' if d.hour < 12 else 'pm'}"


def words(spot: dict, lows: list[dict], tz: ZoneInfo) -> tuple[str, str]:
    """The note's title and text, in English as the server's notes are."""
    title = f"Rock pools at {spot['name']} tomorrow"
    parts = []
    for p in lows:
        m = p["m"]
        parts.append(f"low {'−' if m < 0 else '+'}{abs(m):.2f} m at {_clock(p['at'], tz)}")
    text = (", ".join(parts) + ".")
    text = text[0].upper() + text[1:]
    big = [p for p in lows if (p.get("swell_m") or 0) >= BIG_SWELL_M]
    if big:
        text += f" Swell {max(p['swell_m'] for p in big):.1f} m then: keep off the rock platforms."
    return title, text


def check(path: Path | None = None, now: float | None = None, outdoors: Outdoors | None = None,
          post: Post | None = None) -> tuple[bool, dict]:
    """Tell the owner about tomorrow's good lows at each watched spot, once a day each."""
    now = time.time() if now is None else now
    ask = outdoors or (lambda la, lo: map_outdoors.outdoors(la, lo))
    if post is None:
        from . import notes

        post = notes.post
    told = []
    with _LOCK:
        book = _load(path)
        spots = list(book.get("") or [])
        sent: dict = dict(book.get("_told") or {})
    for spot in spots:
        ok, got = ask(spot["lat"], spot["lon"])
        if not ok:
            continue
        try:
            tz = ZoneInfo(got.get("tz") or "UTC")
        except Exception:  # noqa: BLE001 - an unknown zone reads as UTC
            tz = ZoneInfo("UTC")
        tomorrow = (dt.datetime.fromtimestamp(now, tz) + dt.timedelta(days=1)).date()
        if sent.get(spot["id"]) == tomorrow.isoformat():
            continue
        lows = [p for p in got.get("pools") or []
                if p.get("good") and dt.datetime.fromtimestamp(p["at"], tz).date() == tomorrow]
        if not lows:
            continue
        title, text = words(spot, lows, tz)
        post({"id": f"pools-{spot['id'][3:]}", "title": title, "text": text, "ttl_s": 20 * 3600})
        sent[spot["id"]] = tomorrow.isoformat()
        told += [{"id": spot["id"], "name": spot["name"], **{k: p[k] for k in ("at", "m", "swell_m") if k in p}}
                 for p in lows]
    if told:
        with _LOCK:
            book = _load(path)
            ids = {s["id"] for s in book.get("") or []}
            book["_told"] = {k: v for k, v in sent.items() if k in ids}
            _save(book, path)
    return True, {"told": told}
