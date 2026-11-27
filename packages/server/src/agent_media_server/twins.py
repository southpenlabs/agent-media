"""One history across a chat's two copies: the server's bridge and the phone's
(server-contract.md §6.61 "One history"; sasonica-app mail #1416, David,
28 Nov 2026: "Ask agent-media to stitch history").

One account can be bridged on the server (a Matrix room) and by Sasonica
Bridges on the phone (a Local chat, `local:<network>:<chat id>`): twins
(§6.61 "Homes"). Each side keeps its own copy, and a new sign-in has only
what the network backfills (Google Messages: about 50 messages a chat), so
a chat whose account moved looks cut short. Here each copy's log reads on,
past its own first message, into the other's older messages, the overlap
cut by time at that first message — the shape of the retired login's merge
(matrix.Room.attach retired, 27 Nov 2026):

- a room's log, once all of the room is held, goes on into its twin's Full
  copy messages (local_messages.py; none unless the phone sends them);
- a Local chat's log, once its held messages are read, goes on into its
  twin room's older history.

Messages from the other copy carry `earlier: "<its session>"`, are shown
as this copy's people (in a one-to-one chat the other copy's person reads
as this one's), and are read-only here: replying to, editing or reacting
to one is 409 (matrix.Room._foreign, local_send via `foreign`).

**Pairing** is the server's own, as the app's lib/twins.ts pairs rows: a
room's `network.service` and `network.chat` (matrix.with_chat) name the
Local id. Both directions are read whatever the homes say, so changing a
home (POST /local/settings homes) moves nothing and loses nothing: the new
home's chat reads on into the old one's.
"""

from __future__ import annotations

import os
import threading

from agent_media_core import failures as _failures

#: A Local chat → the room message ids its log has shown (read-only there).
_FOREIGN: dict[str, set[str]] = {}
_LOCK = threading.Lock()


def local_of(r) -> str:
    """The Local id of a room's twin, or "" (no service or chat, or no phone
    has listed it)."""
    from . import local_chats, matrix

    net = matrix.with_chat(r.network, r.channel) or {}
    service, chat = net.get("service"), net.get("chat")
    if not service or not chat:
        return ""
    sid = f"local:{service}:{chat}"
    return sid if local_chats.known(sid) else ""


def room_of(session: str):
    """The room twinned with a Local chat, loaded, or None."""
    from . import matrix

    parts = session.split(":", 2)
    if len(parts) != 3:
        return None
    _, service, chat = parts
    service_of = _service_of()
    if service_of is None:
        return None
    for r in list(matrix._ROOMS.values()):
        if not r.channel or r.channel[2] != chat or not r.network:
            continue
        net = matrix.with_chat(r.network, r.channel, service_of=service_of) or {}
        if net.get("service") == service:
            return matrix.room(r.thread)
    return None


def _service_of():
    from . import bridge_addon

    return getattr(bridge_addon.bridges(), "service_of", None)


def foreign(chat: str, mid: str) -> bool:
    """Whether `mid` is a twin room's message shown in Local chat `chat`."""
    with _LOCK:
        return mid in _FOREIGN.get(chat, ())


READ_ONLY = {"error": "that message is in this chat's other copy: it can be read, "
                      "not changed or answered", "status": 409, "code": "other_copy"}


# --- a room reads on into the phone's copy -----------------------------------------


def _room_person(r) -> str:
    """A one-to-one room's other person, or "" (a group, or no one)."""
    from . import contacts

    try:
        others = contacts._others(r, contacts.own_ghosts())
    except Exception as _exc:  # noqa: BLE001 — no card book: the members as they are
        _failures.swallowed(_exc)
        me = (r.owner, os.environ.get("MATRIX_SAM_ID") or "")
        with r.lock:
            others = [u for u in r.members if u not in me]
    return others[0] if len(others) == 1 else ""


def room_envelope(r, out: dict, *, limit: int, before: str = "") -> dict:
    """A room's log answer (matrix.envelope's `out`), read on into its twin's
    Full copy once all of the room is held."""
    from . import local_messages

    if out.get("around") or not local_messages._exists():
        return out
    chat = local_of(r)
    if not chat:
        return out
    with r.lock:
        if r.more():
            return out
        held = [m["at"] for m in r.messages if m.get("at")]
        ids = {m["id"] for m in r.messages}
    if before and before in ids:
        if len(out["messages"]) >= limit or out["older"]:
            return out
        room_part, want, end_id = out["messages"], limit - len(out["messages"]), ""
    elif before:
        room_part, want, end_id = [], limit, before
    elif len(out["messages"]) < limit and not out["older"]:
        room_part, want, end_id = out["messages"], limit - len(out["messages"]), ""
    else:
        return out
    cut = min(held) if held else None
    conn = local_messages._connect()
    order = [i for i, at in conn.execute(
        f"SELECT id, at FROM msgs WHERE chat=? AND kind IN ({','.join('?' * len(local_messages.SHOWN))}) "
        "ORDER BY at, seq", (chat, *local_messages.SHOWN)) if cut is None or at < cut]
    if end_id:
        if end_id not in order:
            return out
        end = order.index(end_id)
    else:
        end = len(order)
    start = max(0, end - want)
    msgs = local_messages._view(conn, chat, local_messages._rows(conn, chat, order[start:end]))
    if not msgs:
        return out
    person = _room_person(r)
    extra = r._sender_extra(person) if person else {}
    for m in msgs:
        m.pop("local", None)
        m["earlier"] = chat
        if m["role"] == "user":
            m["sender"] = {"id": r.owner, "name": r.members.get(r.owner) or "You"}
        elif person:
            name = r.members.get(person) or m["sender"]["name"]
            m["sender"] = {"id": person, "name": name, **extra}
            m["peer"] = {"name": name}
    with r.lock:
        r._foreign.update(m["id"] for m in msgs)
    out["messages"] = msgs + list(room_part)
    out["older"] = start > 0
    out["twin"] = chat
    return out


# --- a Local chat reads on into the room's -----------------------------------------


def _local_person(conn, chat: str) -> dict | None:
    """A one-to-one Local chat's other person as its newest message names
    them: `{id, name, avatar?}`, or None."""
    from . import local_chats, local_messages

    if (local_chats.record(chat) or {}).get("kind") != "person":
        return None
    row = conn.execute(
        "SELECT sender_id, sender FROM msgs WHERE chat=? AND mine=0 AND "
        f"kind IN ({','.join('?' * len(local_messages.SHOWN))}) ORDER BY at DESC, seq DESC LIMIT 1",
        (chat, *local_messages.SHOWN)).fetchone()
    if not row or not (row[0] or row[1]):
        return None
    who = {"id": row[0] or row[1], "name": row[1] or ""}
    tag = local_messages._avatar_tags(conn, chat).get(row[0]) if row[0] else None
    if tag:
        who["avatar"] = {"url": local_messages.avatar_url(chat, row[0]), "etag": tag}
    return who


def local_envelope(chat: str, out: dict, *, limit: int, before: str = "") -> dict:
    """A Local chat's log answer (local_messages.envelope's `out`), read on
    into its twin room's history older than the chat's first message."""
    from . import local_messages

    if out.get("around"):
        return out
    shown = {m["id"] for m in out["messages"]}
    if before and not out["messages"] and not _held(chat, before):
        room_part, want, end_id = [], limit, before
    elif len(out["messages"]) < limit and not out["older"]:
        room_part, want, end_id = out["messages"], limit - len(out["messages"]), ""
    else:
        return out
    r = room_of(chat)
    if r is None:
        return out
    first = local_messages.span(chat)[0] if local_messages._exists() else None
    with r.lock:
        while True:
            older = [m for m in r.messages
                     if m.get("at") and (first is None or m["at"] < first) and m["id"] not in shown]
            end = len(older)
            if end_id:
                end = next((i for i, m in enumerate(older) if m["id"] == end_id), -1)
                if end < 0 and not r.more():
                    return out
            if end >= want or not r.more() or not r.older():
                break
        if end < 0:
            return out
        start = max(0, end - want)
        msgs = r._view(older[start:end])
        more = start > 0 or r.more()
    if not msgs:
        return out
    person = _local_person(local_messages._connect(), chat) if local_messages._exists() else None
    for m in msgs:
        m["earlier"] = r.thread
        m.pop("read_by", None)
        if m["role"] == "user":
            m["sender"] = {"id": "me", "name": "You"}
        elif person:
            m["sender"] = dict(person)
            m["peer"] = {"name": person["name"]}
    with _LOCK:
        _FOREIGN.setdefault(chat, set()).update(m["id"] for m in msgs)
    out["messages"] = msgs + list(room_part)
    out["older"] = more
    out["twin"] = r.thread
    return out


def _held(chat: str, mid: str) -> bool:
    from . import local_messages

    if not local_messages._exists():
        return False
    return local_messages._connect().execute(
        "SELECT 1 FROM msgs WHERE chat=? AND id=?", (chat, mid)).fetchone() is not None


def _reset_for_tests() -> None:
    with _LOCK:
        _FOREIGN.clear()
