"""`POST /session/mark` — a chat pinned to the top, or marked unread
(server-contract.md §6.38).

From the list of what the bridged apps do (David, 20 Oct 2026, item 7,
per-chat controls; 29 Oct: "OK let's do it"), in every kind of chat:

* **Pin to top**: the chat sits above the others in the list, the most
  recently pinned first, until unpinned. (Not `/session/pin`, which keeps a
  session from the idle reaper.)
* **Mark unread**: the chat shows as unread until it is opened again. In a
  Matrix room it is also the owner's `m.marked_unread` room account data
  (and the older `com.famedly.marked_unread`), which a bridge that handles
  it carries to the network as its own "mark as unread". Only
  mautrix-whatsapp does (its binary has `HandleMarkedUnread`; the Meta,
  Instagram, Google Messages, Telegram and Teams bridges do not, checked
  4 Nov 2026), so the answer and the row say whether the network will show
  it (`carried` / `mark_carried`: true, false, or null for a chat with no
  network), for the app to label the item "(here only)" and say so after
  the tap (David, 4 Nov 2026: "how do we indicate that in the app?").
  `[marks] carried = ["whatsapp", …]` in config.toml names the networks
  whose bridge carries it, when a newer bridge adds it.

Kept here, so every device shows the same: `/targets` rows carry `top` (when
pinned, epoch seconds, or null), `marked_unread` and `mark_carried`.
Opening the chat in the app sends `unread: false`.

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

# The networks whose bridge carries `m.marked_unread` to the network itself.
CARRIED_DEFAULT = frozenset({"whatsapp"})


def carried_networks() -> frozenset[str]:
    """`[marks] carried` in config.toml, else the bridges known to carry it."""
    try:
        from agent_media_core import config

        got = (config.load().get("marks") or {}).get("carried")
    except Exception:  # noqa: BLE001 — a bad config means the default
        got = None
    if isinstance(got, list):
        return frozenset(str(n).strip().lower() for n in got if str(n).strip())
    return CARRIED_DEFAULT


def carried(network: dict | None) -> bool | None:
    """Whether the network a bridge carries this chat for will show the
    mark: null for a chat with no network (an agent's, a plain room)."""
    if not isinstance(network, dict) or not network.get("id"):
        return None
    return str(network["id"]).lower() in carried_networks()


def add_marks(rows: list[dict]) -> list[dict]:
    """`top`, `marked_unread` and `mark_carried` on each `/targets` row."""
    held = STORE.all()
    for row in rows:
        m = held.get(row.get("session") or "")
        got = m if isinstance(m, dict) else {}
        row["top"] = got.get("top")
        row["marked_unread"] = bool(got.get("unread"))
        row["mark_carried"] = carried(row.get("network"))
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
    "marked_unread", "carried"}`."""
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
                  "marked_unread": bool(row.get("unread")),
                  "carried": carried(room.network) if room is not None else None}
