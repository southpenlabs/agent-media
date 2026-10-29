"""`POST /session/read` — telling a chat's network it has been read
(server-contract.md §6.32).

David, 20 Oct 2026, from the list of what the bridged apps do: reading a
chat here should mark it read in WhatsApp or Messenger, so the phone stops
nagging; and "it would also be nice to be able to read something without
marking it read sometimes". 29 Oct 2026, asked how: "A setting per chat".

Each room has a mode:

* `"open"`: opening the chat, and each message read while it is open,
  marks it read on the network.
* `"reply"`: opening it tells nobody — a peek. Replying marks it read, and
  so does Mark read (in the ⋮, and under the newest message).

There is a default for every room (David, 29 Oct 2026: "Can Mark read when
opened also be a system wide setting?"), `"open"` unless changed in
Settings (`/settings/read-receipts`); a room set in its own ⋮ keeps its
own until set back to the default (`mode: "default"`).

The app reports what happened (`why`: `"open"`, `"reply"` or `"button"`);
the rule is here. Marking read is the owner's `m.read` receipt
(matrix.py `Room.read`), which the bridge carries to the network. The
app's own unread dots are its business and are not changed by the mode.

An agent's chat has no network to tell: `/session/read` answers it with
`{"mode": null}` and does nothing.

Stored in `<state_dir>/read_receipts.json` as `{"<thread>": {"mode"},
"*": {"mode"}}` (`_jsonmap.JsonMap`): `"*"` is the default, absent when it
is `"open"`; a room that follows the default has no entry.
"""

from __future__ import annotations

from . import auth, sessions
from ._jsonmap import JsonMap

STORE = JsonMap("read_receipts.json")
MODES = ("open", "reply")
WHYS = ("open", "reply", "button")
#: The key of the default for every room: no thread id looks like it.
DEFAULT = "*"


def _own(key: str) -> str | None:
    row = STORE.get(key)
    mode = row.get("mode") if isinstance(row, dict) else None
    return mode if mode in MODES else None


def default_mode() -> str:
    return _own(DEFAULT) or "open"


def own(thread: str) -> bool:
    """Whether the room has a mode of its own, not the default's."""
    return _own(thread) is not None


def mode_of(thread: str) -> str:
    return _own(thread) or default_mode()


def set_mode(key: str, mode: str) -> None:
    """A room's mode, or the default's (`key` `DEFAULT`). `"default"` puts a
    room back on the default; the default set to `"open"` is no entry."""
    def fn(rows: dict) -> bool:
        if mode == "default" or (key == DEFAULT and mode == "open"):
            return rows.pop(key, None) is not None
        if (rows.get(key) or {}).get("mode") == mode:
            return False
        rows[key] = {"mode": mode}
        return True
    STORE.update(fn)


def settings(bearer: str, mode: str = "") -> tuple[bool, dict]:
    """`GET` / `POST /settings/read-receipts {"mode"}` → `{"mode"}`: the
    default for every room that has none of its own."""
    user, err = auth.gate(bearer)
    if not user:
        return False, err
    if mode:
        if mode not in MODES:
            return False, {"error": f"mode is one of {', '.join(MODES)}", "status": 400}
        set_mode(DEFAULT, mode)
    return True, {"mode": default_mode()}


def told(mode: str, why: str) -> bool:
    """Whether a read for `why` is told to the network in `mode`."""
    return why != "open" or mode == "open"


def session_read(session: str, bearer: str, *, mid: str = "", why: str = "",
                 mode: str = "") -> tuple[bool, dict]:
    """`{"session", "mode"?, "why"?, "id"?}` → `{"session", "mode", "own",
    "read", "up_to"}`.

    `mode` sets the chat's mode (`"default"` puts it back on the default),
    and marks nothing unless `why` is given too. `why` is what happened;
    `id` the newest message read (default: the room's newest). `read` is
    the message the network was told of now, or null when nothing was told
    (a peek, or already told); `up_to` the last one it has been told of
    since the server started, or null."""
    session = (session or "").strip()
    if not sessions._SESSION.fullmatch(session):
        return False, {"error": "not a session id", "status": 400}
    if mode and mode not in MODES + ("default",):
        return False, {"error": f"mode is one of {', '.join(MODES)}", "status": 400}
    if why and why not in WHYS:
        return False, {"error": f"why is one of {', '.join(WHYS)}", "status": 400}
    user, err = auth.gate(bearer)
    if not user:
        return False, err
    from . import matrix

    room = matrix.room(session)
    if room is None:
        return True, {"session": session, "mode": None, "own": False, "read": None,
                      "up_to": None}
    if mode:
        set_mode(session, mode)
    now = mode_of(session)
    read = None
    if why and told(now, why):
        ok, detail = room.read((mid or "").strip())
        if not ok:
            return False, detail
        read = detail.get("read")
    return True, {"session": session, "mode": now, "own": own(session), "read": read,
                  "up_to": room.read_sent or None}
