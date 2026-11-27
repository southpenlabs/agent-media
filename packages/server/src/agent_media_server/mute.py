"""Mute: a chat's notifications off, for a while or until turned off.

David, 18 Nov 2026: "mute ... a notification sound ... do we even do that?
... maybe we should?". A per-chat mute of notifications, separate from the
speech level (`/session/priority`, which says whether a reply is spoken)
and from Quiet for… (quiet.py, which holds a busy thread's place in the list
and keeps its replies from being news, then tells what came meanwhile). A
muted chat is listed, sorted and counted unread as ever; only the
notifications go: the phone posts no "New reply" for it and no sound, and
the server tells nothing of it — nothing is held back for later either.

`POST /session/mute {"session", "muted": bool, "until"?: epoch}` for any
chat: an agent thread, a Matrix room, a listed `local:` id (§6.61).
`muted: true` with an `until` in the future is "until then", without one
"until I turn it off"; `muted: false` ends it. Rows on `/targets`,
`/conversations` and `GET /local/chats`' settings carry
`muted: {"until": epoch | null} | false`.

Where the server's notifications skip a muted chat:

* the `rooms` frame (room_notices.py): someone's message in a muted room
  is not told, nor kept aside for when a quiet ends;
* the `sessions` frame (session_events.py): a muted thread's row carries
  `muted: true`, so the phone's notifier posts no "New reply" and a "Needs
  you" without sound.

A Bring back… reminder (remind.py) is still told: it is one David set.

Kept on the server so every device agrees, in `<state_dir>/mute.json` as
`{"<session>": {"until": epoch | null, "set": epoch}}` (`_jsonmap.JsonMap`).
A row whose `until` has passed is ignored on read and pruned on the next
write.
"""

from __future__ import annotations

import time

from . import auth, sessions
from ._jsonmap import JsonMap

MUTE = JsonMap("mute.json")


def _live(row, now: float) -> bool:
    if not isinstance(row, dict) or not isinstance(row.get("set"), (int, float)):
        return False
    until = row.get("until")
    return until is None or (isinstance(until, (int, float))
                             and not isinstance(until, bool) and until > now)


def muted(now: float | None = None) -> dict[str, dict]:
    """Every chat muted now: `{session: {"until": epoch | null}}`."""
    now = time.time() if now is None else now
    return {k: {"until": v.get("until")} for k, v in MUTE.all().items() if _live(v, now)}


def mute_of(session: str, now: float | None = None) -> dict | None:
    """`{"until"}` while `session` is muted, else None."""
    now = time.time() if now is None else now
    row = MUTE.get(session)
    return {"until": row.get("until")} if _live(row, now) else None


def is_muted(session: str, now: float | None = None) -> bool:
    return mute_of(session, now) is not None


def set_mute(session: str, on: bool, until: float | None = None) -> bool:
    """Mute `session` until `until` (None: until turned off), or end it.
    Expired rows go with any write. True when anything changed."""
    now = time.time()
    if not on:
        return MUTE.drop(session)

    def fn(rows: dict) -> bool:
        for k in [k for k, v in rows.items() if not _live(v, now)]:
            del rows[k]
        rows[session] = {"until": None if until is None else round(float(until), 3),
                         "set": round(now, 3)}
        return True
    return MUTE.update(fn)


def add_mute(rows: list[dict]) -> list[dict]:
    """`muted` on every row: `{"until"}` while muted, else false."""
    hushed = muted()
    for row in rows:
        row["muted"] = hushed.get(str(row.get("session") or "")) or False
    return rows


def session_mute(session: str, body: dict, bearer: str) -> tuple[bool, dict]:
    """`POST /session/mute {"session", "muted", "until"?}`. Gated and refused
    as `/session/quiet` is: 400 for a bad id, a `muted` that is not a bool or
    a bad `until`, 404 for a chat nothing knows (a Matrix room counts; a
    `local:` id once a phone has listed it)."""
    from . import local_chats, matrix

    session = (session or "").strip()
    local = local_chats.is_local(session)
    if not (sessions._SESSION.fullmatch(session) or local):
        return False, {"error": "not a session id", "status": 400}
    on = body.get("muted")
    if not isinstance(on, bool):
        return False, {"error": "muted must be true or false", "status": 400}
    until = body.get("until")
    now = time.time()
    if on and until is not None and (isinstance(until, bool)
                                     or not isinstance(until, (int, float)) or until <= now):
        return False, {"error": "until must be a time to come or null", "status": 400}
    user, err = auth.gate(bearer)
    if not user:
        return False, err
    if local:
        if not local_chats.known(session):
            return False, {"error": "no such Local chat", "status": 404}
    elif not (sessions.live_sessions().get(session) or sessions.session_exists(session)
              or sessions._folder_for_session(session)
              or matrix.room(session) is not None):
        return False, {"error": f"no such session {session[:8]}", "status": 404}
    try:
        set_mute(session, on, until if on else None)
    except OSError as e:
        return False, {"error": f"could not save the mute ({e})", "status": 500}
    if not local:
        from . import bridge_mute

        bridge_mute.soon(session)      # Telegram muted too (bridge_mute.py)
    return True, {"session": session, "muted": mute_of(session) or False}
