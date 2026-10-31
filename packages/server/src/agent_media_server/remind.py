"""Bring back…: a thread that reappears as unread at a date and time.

David, 30 Oct 2026 (the opposite of Quiet for…, quiet.py): "I'd like to be
able to make a thread reappear as unread at a certain date and time."

`POST /session/remind {"session", "at": epoch | null | false, "held"?}`: from
then until `at` the thread is held down — in the list it sits where it did
(`held`, its activity time when it was set), and nothing of it is news: no dot,
no notice, not in Home's Unread replies — exactly as under a quiet. At `at` it
comes back as unread: it floats to the top by `max(last activity, at)`, is in
Home's Unread replies, and the phone is told (a `reminders` frame on
`/sessions/events?remind=1`, session_events.py; the phone's notifier posts
"Back · <title>"), even with the app shut. `null` or `false` cancels it; the
app cancels a due one when the thread is opened (the seen-mark is the app's).

The rows (`/targets`, `/conversations`, `/dashboard` `recent`) carry
`remind: {"at", "held"} | null` and `reminded: true` once it is due. While it
is pending the row's `quiet` is `{"until": at, "at": held, "remind": true}`
unless the thread is really quiet too (that quiet shows as it is); once due,
`quiet` is null whatever the thread's own quiet says — the due time lifts it
regardless. Opening the thread, which cancels the reminder, lets the quiet
take hold again.

It also **unarchives** (David, 31 Oct 2026: "does bring back also
unarchive?" — yes, when due): setting a reminder on an archived thread
leaves it archived; when it comes due the server takes the archive flag off
(`archive.py`, the store `/session/archive` uses), once per reminder
(`fired`), so it is back in the list as well as unread — with the app shut
too, as the phone's stream asks every few seconds. Archiving it again after
that is left alone.

Kept on the server so every device agrees and it fires when the app is shut,
in `<state_dir>/remind.json` as `{"<session>": {"at", "held", "set", "fired"?}}`
(`_jsonmap.JsonMap`). A reminder is at most a year off.
"""

from __future__ import annotations

import time

from . import auth, sessions
from ._jsonmap import JsonMap

STORE = JsonMap("remind.json")

#: The furthest a reminder may be set (a year).
MAX_S = 366 * 86400


def _ok(row) -> bool:
    return (isinstance(row, dict) and isinstance(row.get("at"), (int, float))
            and not isinstance(row.get("at"), bool))


def reminders() -> dict[str, dict]:
    """Every reminder held: `{session: {"at", "held"}}`, pending and due."""
    return {k: {"at": float(v["at"]), "held": float(v.get("held") or v.get("set") or v["at"])}
            for k, v in STORE.all().items() if _ok(v)}


def pending(now: float | None = None) -> dict[str, dict]:
    now = time.time() if now is None else now
    return {k: v for k, v in reminders().items() if v["at"] > now}


def due(now: float | None = None) -> dict[str, dict]:
    """Reminders whose time has come and that nobody has opened yet (each
    newly due one unarchived first, `fire_due`)."""
    now = time.time() if now is None else now
    fire_due(now)
    return {k: v for k, v in reminders().items() if v["at"] <= now}


def fire_due(now: float | None = None) -> list[str]:
    """Each reminder that has come due and not yet fired: its thread
    unarchived, and the reminder marked `fired` so it happens once (an
    archive made after it came back is left alone). The sessions it fired."""
    now = time.time() if now is None else now
    todo = [k for k, v in STORE.all().items() if _ok(v) and v["at"] <= now and not v.get("fired")]
    if not todo:
        return []
    from . import archive

    for k in todo:
        archive.unarchive_quietly(k)

    def fn(rows: dict) -> bool:
        changed = False
        for k in todo:
            r = rows.get(k)
            if _ok(r) and r["at"] <= now and not r.get("fired"):
                rows[k] = {**r, "fired": round(now, 3)}
                changed = True
        return changed
    STORE.update(fn)
    return todo


def due_key(now: float | None = None) -> tuple:
    """Which reminders are due: the stream's watcher wakes when this changes."""
    return tuple(sorted((k, v["at"]) for k, v in due(now).items()))


def set_remind(session: str, at, held: float | None = None) -> bool:
    """Remind at `at` (epoch), holding the thread at `held` (default now); a
    falsy `at` cancels. True when anything changed."""
    if not at:
        return STORE.drop(session)
    now = time.time()
    value = {"at": round(float(at), 3), "held": round(float(held if held else now), 3),
             "set": round(now, 3)}

    def fn(rows: dict) -> bool:
        old = rows.get(session)
        # Moved: the place it holds stays the one it had when first set.
        if _ok(old) and old["at"] > now:
            value["held"] = old.get("held") or value["held"]
        if rows.get(session) == value:
            return False
        rows[session] = value
        return True
    return STORE.update(fn)


def add_remind(rows: list[dict]) -> list[dict]:
    """`remind` and `reminded` on every row, and `quiet` as they say (above)."""
    now = time.time()
    # Come due since the last look: unarchived now, so the row says so too.
    fired = set(fire_due(now))
    held = reminders()
    for row in rows:
        if str(row.get("session") or "") in fired:
            row["archived"] = False
        r = held.get(str(row.get("session") or ""))
        if r is None:
            row["remind"], row["reminded"] = None, False
            continue
        row["remind"] = {"at": r["at"], "held": r["held"]}
        row["reminded"] = r["at"] <= now
        if row["reminded"]:
            row["quiet"] = None
        elif not row.get("quiet"):
            row["quiet"] = {"until": r["at"], "at": r["held"], "remind": True}
    return rows


def session_remind(session: str, body: dict, bearer: str) -> tuple[bool, dict]:
    """`POST /session/remind {"session", "at", "held"?}`. Gated and refused as
    `/session/quiet` is: 400 for a bad id, an `at` that is not a time to come
    (or null or false, which cancel) or more than a year off, 404 for a
    session nothing knows. `held`, optional: the thread's activity time as
    the caller sorts it, which it keeps meanwhile (default now)."""
    session = (session or "").strip()
    if not sessions._SESSION.fullmatch(session):
        return False, {"error": "not a session id", "status": 400}
    at = body.get("at")
    held = body.get("held")
    now = time.time()
    if at is not None and at is not False:
        if isinstance(at, bool) or not isinstance(at, (int, float)) or at <= now:
            return False, {"error": "at must be a time to come, null or false", "status": 400}
        if at > now + MAX_S:
            return False, {"error": "at must be within a year", "status": 400}
    if held is not None and (isinstance(held, bool) or not isinstance(held, (int, float))
                             or held <= 0 or held > now + 60):
        return False, {"error": "held must be a time that has passed", "status": 400}
    user, err = auth.gate(bearer)
    if not user:
        return False, err
    from . import matrix

    if not (sessions.live_sessions().get(session) or sessions.session_exists(session)
            or sessions._folder_for_session(session)
            or matrix.room(session) is not None):
        return False, {"error": f"no such session {session[:8]}", "status": 404}
    try:
        set_remind(session, at or None, held)
    except OSError as e:
        return False, {"error": f"could not save the reminder ({e})", "status": 500}
    from . import quiet

    # The row as the lists will carry it, so the caller shows it at once.
    row = add_remind([{"session": session, "quiet": quiet.quiet_of(session)}])[0]
    return True, {"session": session, "remind": row["remind"], "reminded": row["reminded"],
                  "quiet": row["quiet"]}
