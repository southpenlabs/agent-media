"""`POST /session/group` and `POST /session/leave` — a group chat renamed,
or left (server-contract.md §6.41).

From the list of what the bridged apps do (David, 20 Oct 2026, per-chat
controls: "Group info: members, rename, picture, leave"). The members, with
their pictures and cards, are the log's `room` (`Room.info`); renaming sets
the room's name as the owner and leaving leaves it as the owner, which the
bridge makes the WhatsApp or Messenger group's own. Groups only: a chat
with one person has no name of its own to change, and is archived, not
left. A group left here is archived too.
"""

from __future__ import annotations

from . import auth, sessions

NAME_MAX = 100


def _group(session: str, bearer: str):
    session = (session or "").strip()
    if not sessions._SESSION.fullmatch(session):
        return None, {"error": "not a session id", "status": 400}
    user, err = auth.gate(bearer)
    if not user:
        return None, err
    from . import matrix

    room = matrix.room(session)
    if room is None:
        return None, {"error": "not a chat with people", "status": 409}
    if not room.info()["group"]:
        return None, {"error": "only a group can be renamed or left here", "status": 409}
    return room, None


def session_group(session: str, name: str, bearer: str) -> tuple[bool, dict]:
    name = " ".join((name or "").split())
    if not name or len(name) > NAME_MAX:
        return False, {"error": f"a group's name is 1 to {NAME_MAX} characters", "status": 400}
    room, err = _group(session, bearer)
    if err:
        return False, err
    ok, detail = room.rename_group(name)
    return ok, ({"session": session.strip(), **detail} if ok else detail)


def session_leave(session: str, bearer: str) -> tuple[bool, dict]:
    room, err = _group(session, bearer)
    if err:
        return False, err
    ok, detail = room.leave()
    if not ok:
        return False, detail
    from . import archive

    archive.set_archived(session.strip(), True)
    return True, {"session": session.strip(), "left": True, "archived": True}
