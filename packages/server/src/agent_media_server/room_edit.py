"""`POST /session/edit` and `POST /session/unsend` — your own message in a
room, changed or taken back for everyone (server-contract.md §6.39).

From the list of what the bridged apps do (David, 20 Oct 2026, per-chat
controls: "Edit and unsend my own messages"). Any of your messages, not
only the latest: an edit is an `m.replace` (shown "edited"), an unsend a
redaction (the message goes), and the bridge carries either to WhatsApp or
Messenger as its own edit or delete. Rooms only: an agent's chat has Cancel
and Edit on your latest message instead (§6.19).
"""

from __future__ import annotations

from . import auth, sessions

#: Edited words, at most.
TEXT_MAX = 32768


def _room(session: str, bearer: str):
    session = (session or "").strip()
    if not sessions._SESSION.fullmatch(session):
        return None, {"error": "not a session id", "status": 400}
    user, err = auth.gate(bearer)
    if not user:
        return None, err
    from . import matrix

    room = matrix.room(session)
    if room is None:
        return None, {"error": "only a chat with people can be edited this way "
                               "(an agent's chat: Cancel or Edit your latest message)",
                      "status": 409}
    return room, None


def session_edit(session: str, mid: str, text: str, bearer: str) -> tuple[bool, dict]:
    text = (text or "").strip()
    if not (mid or "").strip() or not text:
        return False, {"error": "edit needs the message's id and the new words", "status": 400}
    if len(text) > TEXT_MAX:
        return False, {"error": "too long", "status": 400}
    room, err = _room(session, bearer)
    if err:
        return False, err
    ok, detail = room.edit(mid.strip(), text)
    return ok, ({"session": session.strip(), **detail} if ok else detail)


def session_unsend(session: str, mid: str, bearer: str) -> tuple[bool, dict]:
    if not (mid or "").strip():
        return False, {"error": "unsend needs the message's id", "status": 400}
    room, err = _room(session, bearer)
    if err:
        return False, err
    ok, detail = room.unsend(mid.strip())
    return ok, ({"session": session.strip(), **detail} if ok else detail)
