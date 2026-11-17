"""Latest wins: when each of a chat's flags last changed, so an older change
arriving late does not undo a newer one.

David, 18 Nov 2026 ("agreed"): a chat's settings are kept in step with its
network both ways — a mute, pin, archive or unread changed in WhatsApp comes
back through the phone's bridges as well as going out from Sasonica — and
the latest change wins. So every flag route takes the time the change was
made, `changed_at` (epoch seconds; `at` too where the route has no `at` of
its own: not `/session/quiet`, whose `at` is a place in the list, nor
`/session/remind`, whose `at` is when it comes due), and a change older
than the one stored for that flag of that chat is answered
`{"ok": true, "session", "stale": true, "changed_at": <the stored one>}`
and not applied. Absent, the change is made now.

The flags, one clock each per chat: `/session/mute`, `/session/archive`,
`/session/pin`, `/session/mark`'s `top` and `unread` (each on its own: a
body with one stale and one not applies the newer), `/session/quiet`,
`/session/remind`, `/session/tags`, `/session/priority`, `/session/issue`,
and `/session/read`'s `mode` (a read mark itself is an event, not a flag).

Kept in `<state_dir>/flag-changes.json` as `{"<session>": {"<flag>":
epoch}}` (`_jsonmap.JsonMap`), written only when a change is applied.
"""

from __future__ import annotations

import time

from ._jsonmap import JsonMap

CLOCK = JsonMap("flag-changes.json")

#: route → its flag's name (/session/mark and /session/read: see keys()).
ROUTES = {"/session/mute": "mute", "/session/archive": "archive", "/session/pin": "pin",
          "/session/mark": "mark", "/session/quiet": "quiet", "/session/remind": "remind",
          "/session/tags": "tags", "/session/priority": "priority",
          "/session/issue": "issue", "/session/read": "read"}
#: Routes whose body has an `at` of its own meaning.
OWN_AT = {"/session/quiet", "/session/remind"}
#: How far ahead of this clock a change time may be (a phone's clock drift).
SKEW_S = 60.0
_MARK = {"top": "mark.top", "unread": "mark.unread"}


def keys(path: str, body: dict) -> list[str]:
    """The flags `body` changes on `path`."""
    if path == "/session/mark":
        return [v for k, v in _MARK.items() if body.get(k) is not None]
    if path == "/session/read":
        return ["read.mode"] if body.get("mode") else []
    return [ROUTES[path]] if path in ROUTES else []


def change_time(path: str, body: dict) -> tuple[float | None, str]:
    """`(t, "")`, `(None, "")` when the body names none, or `(None, error)`."""
    t = body.get("changed_at")
    if t is None and path not in OWN_AT:
        t = body.get("at")
    if t is None:
        return None, ""
    if isinstance(t, bool) or not isinstance(t, (int, float)) or t <= 0 \
            or t > time.time() + SKEW_S:
        return None, "changed_at must be a time that has passed"
    return float(t), ""


def check(path: str, body: dict) -> tuple[dict | None, float | None]:
    """`(body to apply, None)`, or `(None, stored)` when every flag it changes
    has a newer change stored. A /session/mark body loses its stale fields."""
    session = str(body.get("session") or "").strip()
    flags = keys(path, body)
    t, _ = change_time(path, body)
    if not session or not flags or t is None:
        return body, None
    held = CLOCK.get(session) or {}
    if not isinstance(held, dict):
        return body, None
    stale = [f for f in flags if isinstance(held.get(f), (int, float)) and held[f] > t]
    if not stale:
        return body, None
    if len(stale) == len(flags):
        return None, max(held[f] for f in stale)
    body = dict(body)
    for k, v in _MARK.items():
        if v in stale:
            body.pop(k, None)
    return body, None


def record(path: str, body: dict) -> None:
    """Note the time of an applied change (the body's, else now)."""
    session = str(body.get("session") or "").strip()
    flags = keys(path, body)
    if not session or not flags:
        return
    t, _ = change_time(path, body)
    t = round(time.time() if t is None else t, 3)

    def fn(rows: dict) -> bool:
        held = rows.get(session)
        held = dict(held) if isinstance(held, dict) else {}
        for f in flags:
            if not isinstance(held.get(f), (int, float)) or held[f] < t:
                held[f] = t
        rows[session] = held
        return True
    CLOCK.update(fn)
