"""`POST /session/mark` — a chat pinned to the top, or marked unread
(server-contract.md §6.38).

From the list of what the bridged apps do (David, 20 Oct 2026, item 7,
per-chat controls; 29 Oct: "OK let's do it"), in every kind of chat:

* **Pin to top**: the chat sits above the others in the list, the most
  recently pinned first, until unpinned. (Not `/session/pin`, which keeps a
  session from the idle reaper.)
* **Mark unread**: the chat shows as unread until it is opened again. In a
  Matrix room it is also the owner's `m.marked_unread` room account data
  (and the older `com.famedly.marked_unread`), which the bridges carry to
  WhatsApp or Messenger as their own "mark as unread".

Kept here, so every device shows the same: `/targets` rows carry `top` (when
pinned, epoch seconds, or null) and `marked_unread`. Opening the chat in
the app sends `unread: false`.

Stored in `<state_dir>/chat-marks.json` as `{"<session>": {"top"?, "unread"?}}`.
"""

from __future__ import annotations

import logging
import time
import urllib.error

from . import auth, driver, sessions
from ._jsonmap import JsonMap

log = logging.getLogger("agent-media.server.chat_marks")

STORE = JsonMap("chat-marks.json")


def add_marks(rows: list[dict]) -> list[dict]:
    """`top` and `marked_unread` on each `/targets` row."""
    held = STORE.all()
    for row in rows:
        m = held.get(row.get("session") or "")
        got = m if isinstance(m, dict) else {}
        row["top"] = got.get("top")
        row["marked_unread"] = bool(got.get("unread"))
    return rows


def _set(session: str, key: str, value) -> None:
    def fn(rows: dict) -> bool:
        r = dict(rows.get(session) or {})
        if value:
            if r.get(key) and key == "top":
                return False
            r[key] = value
        elif key in r:
            r.pop(key)
        else:
            return False
        if r:
            rows[session] = r
        else:
            rows.pop(session, None)
        return True
    STORE.update(fn)


def _room_unread(room, unread: bool) -> None:
    """The room's own flag, which a bridge carries to the network. Its
    failure is logged, not the answer: the mark here still holds."""
    from . import matrix

    token = matrix.owner_token()
    if not token:
        return
    for kind in ("m.marked_unread", "com.famedly.marked_unread"):
        try:
            matrix._put(room.config, f"/user/{matrix._q(room.owner)}/rooms/"
                        f"{matrix._q(room.id)}/account_data/{kind}", {"unread": unread}, token)
        except (urllib.error.URLError, OSError, ValueError) as e:
            log.info("marked_unread %s on %s: %s", kind, room.id, e)


def session_mark(session: str, bearer: str, *, top=None, unread=None) -> tuple[bool, dict]:
    """`{"session", "top"?: bool, "unread"?: bool}` → `{"session", "top",
    "marked_unread"}`."""
    session = (session or "").strip()
    if not sessions._SESSION.fullmatch(session):
        return False, {"error": "not a session id", "status": 400}
    if top is None and unread is None:
        return False, {"error": "mark needs top or unread", "status": 400}
    if (top is not None and not isinstance(top, bool)) or \
            (unread is not None and not isinstance(unread, bool)):
        return False, {"error": "top and unread are true or false", "status": 400}
    user, err = auth.gate(bearer)
    if not user:
        return False, err
    from . import matrix, quick

    room = matrix.room(session)
    if room is None and not (sessions.live_sessions().get(session)
                             or sessions.session_exists(session)
                             or driver.owned_headless(session) or quick.has(session)):
        return False, {"error": f"no such session {session[:8]}", "status": 404}
    if top is not None:
        _set(session, "top", round(time.time(), 3) if top else None)
    if unread is not None:
        before = bool((STORE.get(session) or {}).get("unread"))
        _set(session, "unread", round(time.time(), 3) if unread else None)
        if room is not None and before != unread:
            _room_unread(room, unread)
    row = STORE.get(session) or {}
    return True, {"session": session, "top": row.get("top"),
                  "marked_unread": bool(row.get("unread"))}
