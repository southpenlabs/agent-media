"""Sending to a Local chat from any device, through the phone's one login
(server-contract.md §6.61 "Sending").

Asked by sasonica-app (mail #1191; David, 22 Nov 2026: "It would be nice if
it could be sent from the server too but with the one login"). The network
login lives in the companion on the bridging phone, and there is to be no
second one. So the server holds the outgoing message here; the bridging
phone hears of it on the stream its background notifier already holds open
(`GET /sessions/events?local_send=1`, a `local_send` frame), sends it with
the companion's sendText (or its reaction), and says how it went
(`POST /local/outbox/result`). The message then comes back like any other,
in the phone's next `POST /local/messages` batch as its own ("me"), and the
outgoing row is marked landed and leaves the list.

**Who sends.** A person, on a paired device that may read the chat (not a
guest, not one limited to its folders), at Full copy. Not an agent: the
host's own token (`X-Auth-Token`, what `media` and agents use) is no bearer
here, so it is 401. Agents writing into other people's chats wants a gate
of its own, which David is asked about before any agent path is added.

**Kept** in the `outbox` table of `local-messages.db` (local_messages.py),
so it goes with the chat's messages: all when the dial leaves `full`, a
chat's when it is pruned. A waiting message older than `WAIT_DAYS` fails as
`expired`; a landed, failed or cancelled one is forgotten `KEEP_DAYS` after
it settled.

**States.** `waiting` (held for the phone) → `sending` (the phone took it)
→ `sent` (with the network's event id) or `failed` (with its words). A
waiting one may be cancelled (deleted) by any device that may send; a
failed one is dismissed the same way. `sent` and `failed` are final.
"""

from __future__ import annotations

import re
import secrets
import threading
import time

from . import auth, local_chats, local_messages
from agent_media_core import failures as _failures

#: Characters of an outgoing message.
MAX_TEXT = 8000
#: A reaction's key (an emoji, possibly with modifiers).
MAX_REACT = 32
#: Messages a chat may have waiting at once.
MAX_WAITING = 200
#: A message the phone has not taken this long fails as `expired`.
WAIT_DAYS = 7
#: A settled message is forgotten this long after.
KEEP_DAYS = 7
MAX_ERROR = 500

STATES = ("waiting", "sending", "sent", "failed")
OPEN = ("waiting", "sending")
SEND_FIELDS = frozenset({"chat", "text", "reply_to", "react", "client_id"})
RESULT_FIELDS = frozenset({"id", "state", "event_id", "error"})
_CLIENT = re.compile(r"[A-Za-z0-9_.:-]{1,64}")
_ID = re.compile(r"o_[0-9a-f]{12}")

_COLS = ("id", "chat", "kind", "text", "reply_to", "client_id", "by_device", "at", "state",
         "taken_by", "event_id", "error", "settled", "landed")

_LOCK = threading.Lock()
_version = 0
#: Devices with a stream open asking for `local_send` frames, by count.
_listening: dict[str, int] = {}


def version() -> int:
    return _version


def changed() -> None:
    """The outbox changed: wake every event stream now (session_events)."""
    global _version
    with _LOCK:
        _version += 1
    try:
        from . import session_events

        session_events.poke()
    except Exception as _exc:  # noqa: BLE001 — the watcher's tick catches it anyway
        _failures.swallowed(_exc)


def listening(device: str | None, on: bool) -> None:
    """A stream asking for `local_send` frames opened (or closed) for `device`."""
    if not device:
        return
    with _LOCK:
        n = _listening.get(device, 0) + (1 if on else -1)
        if n > 0:
            _listening[device] = n
        else:
            _listening.pop(device, None)
    changed()


def online(device: str | None) -> bool:
    return bool(device) and _listening.get(device or "", 0) > 0


def _bad(error: str, **extra) -> tuple[bool, dict]:
    return False, {"error": error, "status": 400, **extra}


def _device_name(device_id: str | None) -> str:
    if not device_id:
        return ""
    from . import devices

    try:
        for d in devices.list_devices():
            if d.get("id") == device_id:
                return str(d.get("name") or "")
    except Exception as _exc:  # noqa: BLE001 — a name is a courtesy
        _failures.swallowed(_exc)
    return ""


def _shape(r: dict, names: dict[str, str] | None = None) -> dict:
    names = {} if names is None else names

    def name(d):
        if d not in names:
            names[d] = _device_name(d)
        return names[d]

    rec = local_chats.record(r["chat"]) or {}
    bridge = rec.get("device")
    out = {"id": r["id"], "chat": r["chat"], "kind": r["kind"], "text": r["text"],
           "reply_to": r["reply_to"], "client_id": r["client_id"], "at": r["at"],
           "state": r["state"], "landed": bool(r["landed"]),
           "by": {"device": r["by_device"], "name": name(r["by_device"])},
           "bridge": {"device": bridge, "name": name(bridge), "online": online(bridge)}}
    if r["event_id"]:
        out["event_id"] = r["event_id"]
    if r["error"]:
        out["error"] = r["error"]
    if r["settled"]:
        out["settled_at"] = r["settled"]
    return out


def _select(conn, where: str, args: tuple) -> list[dict]:
    return [dict(zip(_COLS, row)) for row in conn.execute(
        f"SELECT {', '.join(_COLS)} FROM outbox WHERE {where} ORDER BY at, id", args)]


def _tidy(conn, now: float) -> int:
    """Waiting too long → failed `expired`; settled long ago → forgotten."""
    n = conn.execute("UPDATE outbox SET state='failed', error='expired', settled=? "
                     "WHERE state='waiting' AND at<?", (now, now - WAIT_DAYS * 86400)).rowcount
    n += conn.execute("DELETE FROM outbox WHERE state IN ('sent', 'failed') AND settled<?",
                      (now - KEEP_DAYS * 86400,)).rowcount
    return n


def _may_send(bearer: str) -> tuple[bool, dict, str]:
    ok, err = local_messages.may_read(bearer)
    if not ok:
        return False, err, ""
    device = auth.device_id(bearer)
    if not device:
        return False, {"error": "only a paired device sends to a Local chat", "status": 403,
                       "code": "not_a_device"}, ""
    if not local_messages.enabled():
        return False, dict(local_messages.NOT_ENABLED), ""
    return True, {}, device


# --- POST /local/send -------------------------------------------------------------


def send(body, bearer: str, now: float | None = None) -> tuple[bool, dict]:
    """`POST /local/send {"chat", "text" | "react", "reply_to"?, "client_id"?}`:
    held for the bridging phone. `{outgoing, duplicate}`."""
    ok, err, device = _may_send(bearer)
    if not ok:
        return False, err
    if not isinstance(body, dict):
        return _bad("the body must be an object")
    extra = sorted(set(body) - SEND_FIELDS)
    if extra:
        return _bad(f"not a field of a message to send: {extra[0]}", code="field_not_accepted",
                    field=extra[0])
    chat = body.get("chat")
    if not isinstance(chat, str) or not local_chats.is_local(chat):
        return _bad("chat is a Local chat's id, local:<network>:<id>")
    text, react, reply_to = body.get("text"), body.get("react"), body.get("reply_to")
    if (text is None) == (react is None):
        return _bad("one of text (a message) or react (an emoji on reply_to)")
    if reply_to is not None and (not isinstance(reply_to, str)
                                 or not local_messages._EVENT.fullmatch(reply_to)):
        return _bad("reply_to is the event id of a message in the chat")
    if text is not None:
        if not isinstance(text, str) or not text.strip():
            return _bad("text is the message's words")
        if len(text) > MAX_TEXT:
            return _bad(f"text is at most {MAX_TEXT} characters", code="too_long",
                        limit=MAX_TEXT)
        kind, words = "text", text.replace("\x00", "")
    else:
        if not isinstance(react, str) or not react.strip() or len(react) > MAX_REACT:
            return _bad(f"react is an emoji, at most {MAX_REACT} characters")
        if not reply_to:
            return _bad("a reaction names the message it is on in reply_to")
        kind, words = "reaction", react.strip()
    cid = body.get("client_id")
    if cid is not None and (not isinstance(cid, str) or not _CLIENT.fullmatch(cid)):
        return _bad("client_id is 1-64 letters, digits or _ . : -")
    rec = local_chats.record(chat)
    if rec is None:
        return False, {"error": "no such chat: a phone has not listed it", "status": 404,
                       "code": "not_found"}
    if rec.get("gone"):
        return False, {"error": "the phone no longer lists this chat", "status": 409,
                       "code": "chat_gone"}
    if not rec.get("device"):
        return False, {"error": "no device bridges this chat", "status": 409,
                       "code": "no_bridge"}
    now = time.time() if now is None else now
    with local_messages._WRITE:
        conn = local_messages._connect()
        conn.execute("BEGIN IMMEDIATE")
        try:
            tidied = _tidy(conn, now)
            if cid is not None:
                got = _select(conn, "by_device=? AND client_id=?", (device, cid))
                if got:
                    conn.execute("COMMIT")
                    if tidied:
                        changed()
                    return True, {"outgoing": _shape(got[0]), "duplicate": True}
            waiting = conn.execute("SELECT count(*) FROM outbox WHERE chat=? AND state='waiting'",
                                   (chat,)).fetchone()[0]
            if waiting >= MAX_WAITING:
                conn.execute("COMMIT")
                return False, {"error": f"{MAX_WAITING} messages are already waiting for the phone",
                               "status": 429, "code": "too_many", "limit": MAX_WAITING}
            oid = "o_" + secrets.token_hex(6)
            conn.execute("INSERT INTO outbox(id, chat, kind, text, reply_to, client_id, by_device, "
                         "at, state) VALUES(?,?,?,?,?,?,?,?, 'waiting')",
                         (oid, chat, kind, words, reply_to, cid, device, round(now, 3)))
            row = _select(conn, "id=?", (oid,))[0]
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise
    changed()
    return True, {"outgoing": _shape(row), "duplicate": False}


# --- what the phone takes ---------------------------------------------------------


def for_device(device: str | None, now: float | None = None) -> list[dict]:
    """Every waiting or sending message of the chats `device` bridges, oldest
    first: the `local_send` frame's list."""
    if not device or not local_messages._exists() or not local_messages.enabled():
        return []
    now = time.time() if now is None else now
    conn = local_messages._connect()
    with local_messages._WRITE:
        tidied = _tidy(conn, now)
    if tidied:
        changed()
    names: dict[str, str] = {}
    return [_shape(r, names) for r in _select(conn, "state IN ('waiting', 'sending')", ())
            if (local_chats.record(r["chat"]) or {}).get("device") == device]


def pending(chat: str) -> list[dict]:
    """A chat's outgoing messages not yet back in its log: waiting, sending,
    sent but not yet landed, failed (until dismissed or forgotten)."""
    if not local_messages._exists():
        return []
    names: dict[str, str] = {}
    return [_shape(r, names) for r in _select(local_messages._connect(),
                                              "chat=? AND landed=0", (chat,))]


def outbox(chat: str, bearer: str) -> tuple[bool, dict]:
    """`GET /local/outbox[?chat=]`: with `chat`, that chat's `pending` to
    anyone who reads it; without, what this device is to send."""
    ok, err = local_messages.may_read(bearer)
    if not ok:
        return False, err
    if chat:
        if not local_chats.is_local(chat):
            return _bad("chat is a Local chat's id, local:<network>:<id>")
        if local_chats.record(chat) is None:
            return False, {"error": "no such chat: a phone has not listed it", "status": 404,
                           "code": "not_found"}
        return True, {"chat": chat, "outgoing": pending(chat)}
    device = auth.device_id(bearer)
    if not device:
        return False, {"error": "only a paired device bridges chats", "status": 403,
                       "code": "not_a_device"}
    return True, {"device": device, "outgoing": for_device(device)}


# --- POST /local/outbox/result ------------------------------------------------------


def result(body, bearer: str, now: float | None = None) -> tuple[bool, dict]:
    """`POST /local/outbox/result {"id", "state", "event_id"?, "error"?}` from
    the device that bridges the message's chat. `{outgoing}`."""
    user, err = auth.gate(bearer)
    if not user:
        return False, err
    if auth.is_guest(bearer):
        return False, {"code": "guest", "status": 403,
                       "error": "a guest does not see the phone's chats"}
    device = auth.device_id(bearer)
    if not device:
        return False, {"error": "only a paired device bridges chats", "status": 403}
    if not isinstance(body, dict):
        return _bad("the body must be an object")
    extra = sorted(set(body) - RESULT_FIELDS)
    if extra:
        return _bad(f"not a field of a result: {extra[0]}", code="field_not_accepted",
                    field=extra[0])
    oid, state = body.get("id"), body.get("state")
    if not isinstance(oid, str) or not _ID.fullmatch(oid):
        return _bad("id is an outgoing message's id, o_<12 hex>")
    if state not in ("sending", "sent", "failed"):
        return _bad("state is sending, sent or failed")
    eid, error = body.get("event_id"), body.get("error")
    if state == "sent":
        if not isinstance(eid, str) or not local_messages._EVENT.fullmatch(eid):
            return _bad("a sent message carries the network's event_id")
    elif eid is not None:
        return _bad("event_id comes with state sent")
    if state == "failed":
        if not isinstance(error, str) or not error.strip():
            return _bad("a failed message carries its error, in words")
        error = error.replace("\x00", "").strip()[:MAX_ERROR]
    elif error is not None:
        return _bad("error comes with state failed")
    if not local_messages._exists():
        return False, {"error": "no such outgoing message", "status": 404, "code": "not_found"}
    now = time.time() if now is None else now
    with local_messages._WRITE:
        conn = local_messages._connect()
        conn.execute("BEGIN IMMEDIATE")
        try:
            got = _select(conn, "id=?", (oid,))
            if not got:
                conn.execute("COMMIT")
                return False, {"error": "no such outgoing message", "status": 404,
                               "code": "not_found"}
            r = got[0]
            if (local_chats.record(r["chat"]) or {}).get("device") != device:
                conn.execute("COMMIT")
                return False, {"error": "only the device that bridges this chat sends its "
                               "messages", "status": 403, "code": "not_bridging"}
            cur = r["state"]
            same = cur == state and (state != "sent" or r["event_id"] == eid)
            if same:
                conn.execute("COMMIT")
                return True, {"outgoing": _shape(r)}
            if cur in ("sent", "failed") or (cur == "sending" and state == "sending"):
                conn.execute("COMMIT")
                return False, {"error": f"already {cur}", "status": 409, "code": "settled",
                               "outgoing": _shape(r)}
            if state == "sending":
                conn.execute("UPDATE outbox SET state='sending', taken_by=? WHERE id=?",
                             (device, oid))
            elif state == "sent":
                # Already back in the log (the batch came first): landed now.
                back = conn.execute("SELECT 1 FROM msgs WHERE chat=? AND id=?",
                                    (r["chat"], eid)).fetchone()
                conn.execute("UPDATE outbox SET state='sent', event_id=?, settled=?, "
                             "taken_by=coalesce(taken_by, ?), landed=? WHERE id=?",
                             (eid, round(now, 3), device, 1 if back else 0, oid))
            else:
                conn.execute("UPDATE outbox SET state='failed', error=?, settled=?, "
                             "taken_by=coalesce(taken_by, ?) WHERE id=?",
                             (error, round(now, 3), device, oid))
            r = _select(conn, "id=?", (oid,))[0]
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise
    changed()
    return True, {"outgoing": _shape(r)}


# --- DELETE /local/send ---------------------------------------------------------------


def cancel(oid: str, bearer: str) -> tuple[bool, dict]:
    """`DELETE /local/send?id=`: a waiting message taken back, or a failed one
    dismissed. 409 `taken` once the phone has it."""
    ok, err = local_messages.may_read(bearer)
    if not ok:
        return False, err
    if not auth.device_id(bearer):
        return False, {"error": "only a paired device sends to a Local chat", "status": 403,
                       "code": "not_a_device"}
    if not isinstance(oid, str) or not _ID.fullmatch(oid):
        return _bad("id is an outgoing message's id, o_<12 hex>")
    if not local_messages._exists():
        return False, {"error": "no such outgoing message", "status": 404, "code": "not_found"}
    with local_messages._WRITE:
        conn = local_messages._connect()
        got = _select(conn, "id=?", (oid,))
        if not got:
            return False, {"error": "no such outgoing message", "status": 404,
                           "code": "not_found"}
        if got[0]["state"] in ("sending", "sent"):
            return False, {"error": f"the phone has it ({got[0]['state']})", "status": 409,
                           "code": "taken", "outgoing": _shape(got[0])}
        conn.execute("DELETE FROM outbox WHERE id=?", (oid,))
    changed()
    return True, {"id": oid, "deleted": True}


def _reset_for_tests() -> None:
    global _version
    with _LOCK:
        _version = 0
        _listening.clear()
