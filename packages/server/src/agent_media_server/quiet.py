"""Quiet for…: a busy thread kept from floating to the top for a while.

David, 26 Oct 2026: "Can we have a way of stopping unread messages from busy
threads floating to the top for a time?" A per-thread, timed quiet. While a
thread is quiet the app sorts it by `at` (its activity time when the quiet
began) instead of its live time, leaves it out of Home's Unread replies, the
unread dot, the arrival notices and any count, and marks its row "Quiet until
3 pm". A question or permission card from it still shows: quiet never hides
what needs you. When it runs out (or is ended) the thread is itself again,
and what came meanwhile is unread — nothing is marked seen.

`POST /session/quiet {"session", "until": epoch | null | false}` sets one:
an epoch is "until then", null "until I turn it off", false ends it. The
`/targets`, `/conversations` and `/dashboard` rows carry
`quiet: {"until", "at"} | null`, and `/dashboard`'s `replies` leaves quiet
threads out.

Kept on the server so every device agrees, in `<state_dir>/quiet.json` as
`{"<session>": {"until": epoch | null, "at": epoch, "set": epoch}}`
(`_jsonmap.JsonMap`). A row whose `until` has passed is ignored on read and
pruned on the next write.
"""

from __future__ import annotations

import time

from . import auth, sessions
from ._jsonmap import JsonMap

QUIET = JsonMap("quiet.json")


def _live(row, now: float) -> bool:
    if not isinstance(row, dict) or not isinstance(row.get("at"), (int, float)):
        return False
    until = row.get("until")
    return until is None or (isinstance(until, (int, float)) and until > now)


def quieted(now: float | None = None) -> dict[str, dict]:
    """Every thread quiet now: `{session: {"until", "at"}}`."""
    now = time.time() if now is None else now
    return {k: {"until": v.get("until"), "at": v["at"]}
            for k, v in QUIET.all().items() if _live(v, now)}


def quiet_of(session: str, now: float | None = None) -> dict | None:
    return quieted(now).get(session)


def set_quiet(session: str, until, at: float | None = None) -> bool:
    """Quiet `session` until `until` (epoch, or None for until turned off),
    sorted as of `at` (default now); `until=False` ends it. Expired rows go
    with any write. True when anything changed."""
    now = time.time()
    if until is False:
        return QUIET.drop(session)

    def fn(rows: dict) -> bool:
        for k in [k for k, v in rows.items() if not _live(v, now)]:
            del rows[k]
        old = rows.get(session)
        # Already quiet: a new end, but the same place in the list — the
        # thread was quiet all along, so its time is still the first one.
        keep = old["at"] if _live(old, now) else None
        rows[session] = {"until": None if until is None else round(float(until), 3),
                         "at": keep if keep is not None else round(float(at if at is not None else now), 3),
                         "set": round(now, 3)}
        return True
    return QUIET.update(fn)


def add_quiet(rows: list[dict]) -> list[dict]:
    """`quiet` on every row: `{"until", "at"}` while quiet, else None."""
    q = quieted()
    for row in rows:
        row["quiet"] = q.get(str(row.get("session") or ""))
    return rows


def session_quiet(session: str, body: dict, bearer: str) -> tuple[bool, dict]:
    """`POST /session/quiet {"session", "until", "at"?}`. Gated and refused as
    `/session/pin` is: 400 for a bad id or a bad `until`, 404 for a session
    nothing knows (a Matrix room counts, as for `/session/priority`).
    `until`: an epoch in the future, null (until turned off) or false (end
    it); absent is null. `at`, optional: the thread's activity time as the
    caller sorts it, which the quiet keeps it at (default now, which is
    where a live thread sorts)."""
    session = (session or "").strip()
    if not sessions._SESSION.fullmatch(session):
        return False, {"error": "not a session id", "status": 400}
    until = body.get("until")
    now = time.time()
    if until is not None and until is not False and (
            isinstance(until, bool) or not isinstance(until, (int, float)) or until <= now):
        return False, {"error": "until must be a time to come, null or false", "status": 400}
    at = body.get("at")
    if at is not None and (isinstance(at, bool) or not isinstance(at, (int, float))
                           or at <= 0 or at > now + 60):
        return False, {"error": "at must be a time that has passed", "status": 400}
    user, err = auth.gate(bearer)
    if not user:
        return False, err
    from . import matrix

    if not (sessions.live_sessions().get(session) or sessions.session_exists(session)
            or sessions._folder_for_session(session)
            or matrix.room(session) is not None):
        return False, {"error": f"no such session {session[:8]}", "status": 404}
    try:
        set_quiet(session, until, at)
    except OSError as e:
        return False, {"error": f"could not save the quiet ({e})", "status": 500}
    return True, {"session": session, "quiet": quiet_of(session)}
