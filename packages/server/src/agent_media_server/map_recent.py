"""`GET /map/recent_places`: the places recent chats' replies pinned.

The Map's "Where to?" box lists these under the device's own recent
destinations, before any typing (David, 19 Nov 2026: "maybe offer
suggestions from recent chats"): a café an agent named this morning is a
tap away. Each is a `[[map:]]` / `[[route:]]` point of a reply
(transcript.py's message `places`, §6.50), the newest first, one per place.

`?limit=8` (1-20). Answer: `{places: [{name, lat, lon, session, chat, msg,
at}]}` — `chat` the thread's title, `msg` the message's id, `at` its time.
Behind the map's gate (map_layers._gate), as a chat's own places layer is.

The chats asked are the newest `CHATS` threads by their transcript's change
time, from the thread list's last build (sessions.py) — never one sealed,
private or with people (Matrix rooms keep no transcript here). Each chat's
places are cached against its transcript's (inode, size, mtime), so a list
asked again costs a stat per chat.
"""
from __future__ import annotations

import math
import threading

#: The newest this many chats are looked in.
CHATS = 25
#: The newest this many messages of each.
MESSAGES = 400
#: Two points of one name nearer than this are one place.
SAME_M = 50.0

_CACHE: dict[str, tuple[tuple, list[dict]]] = {}
_LOCK = threading.Lock()


def _metres(a: dict, b: dict) -> float:
    p1, p2 = math.radians(a["lat"]), math.radians(b["lat"])
    dp, dl = p2 - p1, math.radians(b["lon"] - a["lon"])
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * 6371000.0 * math.asin(math.sqrt(h))


def _rows() -> list[dict]:
    """The thread list's rows as last built (or as the last server saved
    them): this never builds the list itself, which takes seconds."""
    from . import sessions

    b = sessions._BUILDS.get("")
    out = (b or {}).get("out")
    if isinstance(out, dict) and isinstance(out.get("sessions"), list):
        return out["sessions"]
    import json

    try:
        got = json.loads(sessions._saved_path().read_text())
    except (OSError, ValueError):
        return []
    return got.get("sessions") or [] if isinstance(got, dict) else []


def _candidates() -> list[tuple[str, str, tuple]]:
    """`(session, title, state)` of the newest chats with a transcript."""
    from . import transcript

    found = []
    for r in _rows():
        sid = str(r.get("session") or "")
        if not sid or r.get("sealed") or r.get("private") or r.get("harness") == "matrix":
            continue
        try:
            state = transcript.file_state(sid)
        except Exception as e:  # noqa: BLE001 — one unreadable chat leaves the rest
            from agent_media_core import failures

            failures.swallowed(e)
            continue
        if state:
            found.append((sid, str(r.get("title_whole") or r.get("title") or ""), tuple(state)))
    found.sort(key=lambda c: c[2][2], reverse=True)
    return found[:CHATS]


def _chat_places(session: str, title: str, state: tuple) -> list[dict]:
    """A chat's pinned points, newest first, cached against its transcript."""
    with _LOCK:
        hit = _CACHE.get(session)
    if hit and hit[0] == state:
        return [{**p, "chat": title} for p in hit[1]]
    from . import transcript

    got = transcript.messages(session, limit=MESSAGES)
    pts: list[dict] = []
    if got:
        msgs = got[0]
        transcript.strip_markers(msgs)
        for m in reversed(msgs):
            for g in m.get("places") or []:
                for p in g.get("points") or []:
                    if p.get("name") and isinstance(p.get("lat"), (int, float)) and isinstance(p.get("lon"), (int, float)):
                        pts.append({"name": p["name"], "lat": p["lat"], "lon": p["lon"], "session": session,
                                    "msg": m.get("id"), "at": m.get("at") or 0.0})
    with _LOCK:
        _CACHE[session] = (state, pts)
    return [{**p, "chat": title} for p in pts]


def recent_places(limit_raw: str = "") -> tuple[bool, dict]:
    try:
        limit = int(limit_raw) if limit_raw else 8
    except ValueError:
        return False, {"status": 400, "code": "bad_limit", "error": "limit is a number, 1-20"}
    if not 1 <= limit <= 20:
        return False, {"status": 400, "code": "bad_limit", "error": "limit is a number, 1-20"}
    every: list[dict] = []
    for sid, title, state in _candidates():
        every.extend(_chat_places(sid, title, state))
    every.sort(key=lambda p: p["at"], reverse=True)
    out: list[dict] = []
    for p in every:
        if any(q["name"] == p["name"] and _metres(q, p) < SAME_M for q in out):
            continue
        out.append(p)
        if len(out) >= limit:
            break
    return True, {"places": out}


def _reset_for_tests() -> None:
    with _LOCK:
        _CACHE.clear()
