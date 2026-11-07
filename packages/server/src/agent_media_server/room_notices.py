"""People's messages in bridged chats, for the phone's notices (§6.13 `rooms`).

David, 7 Nov 2026: "can we also get people's messages in these bubbles too?
... for them, their profile photo would be ideal". Agent threads notify
from the `sessions` frame (a turn ending); a bridged chat has no turn, so a
new message from someone is kept here and handed to the phones that hold
`GET /sessions/events?rooms=<cursor>` as a `rooms` frame.

What counts is what brings an archived chat back (archive_wake.py): a
message or sticker from neither David (another device) nor the bridge's
bot, not an edit or a notice — and someone's reaction to one of David's
messages (David, 7 Nov 2026: "a reaction should give a bubble pop up too"),
told as `Reacted 👍 to "…"` with `reaction: {"key", "to"}`; a reaction to
someone else's message (a group) is not. Receipts and redactions never get
here. On top of that:

* not while the chat is quiet (quiet.py, "Quiet for…", a standing rule
  included): what comes then stays unread, as in the app, and the newest
  of it is kept aside, one a chat, and told when the quiet ends, with
  `held` saying how many came meanwhile (release_held, each sweep of
  session_events; David, 7 Nov 2026: hold options on the notice — "anything
  you missed shows as one notice"). Kept in memory: a restart forgets it;
* not while it is archived, after archive_wake has had its say (a message
  that wakes the chat is told; one that leaves it archived is not);
* not history: a message older than `FRESH_S` (a bridge's catch-up after a
  restart, the first sync's page) or one already told.

Each is `{"n", "thread", "room", "title", "group", "id", "sender": {"id",
"name"}, "avatar"?: {"url", "etag"}, "text", "at", "suggestions"?}`, `n` the
cursor. Kept in memory, the newest `KEEP`; `n` counts from the process's
start in epoch ms, so a phone's cursor from before a restart is behind the
new head and it is handed what came since rather than nothing.

One-tap answers (room_replies.py) for a message told here: on it as
`suggestions` [{text, source}] when they are ready as it is told (a held
message released after a quiet, mostly), else they come after it as an
`answers` entry of their own, `{"n", "thread", "id", "suggestions"}`, on the
same cursor, so the phone's notice gains them as actions (David, 8 Nov 2026:
answer from anywhere, docs/proposals/2026-11-06-one-tap-answers.md part 6).
A phone that does not read `answers` skips them.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from collections import deque

log = logging.getLogger(__name__)

#: A message older than this when it arrives is history, not news.
FRESH_S = 300.0
#: The most kept.
KEEP = 100
#: Words of a message carried in the frame.
TEXT_MAX = 300

_LOCK = threading.Lock()
_HELD: deque[dict] = deque(maxlen=KEEP)
_SEQ = [int(time.time() * 1000)]
_TOLD: deque[str] = deque(maxlen=KEEP * 4)
#: thread → the newest message that came while it was quiet, and `held`.
_QUIET: dict[str, dict] = {}
#: One-tap answers that came after their message was told (answers_ready).
_ANSWERS: deque[dict] = deque(maxlen=KEEP)


def last_seq() -> int:
    """The cursor's head: the newest message's `n`, or where they start."""
    with _LOCK:
        return _SEQ[0]


def notices(after: int | None) -> dict:
    """`{"last", "messages": [...]}`: those after the cursor, oldest first;
    `after` None (a first connection) is only the head, no backlog. With
    one-tap answers that came since, `answers` too (answers_ready)."""
    with _LOCK:
        head = _SEQ[0]
        if after is None or after >= head:
            return {"last": head, "messages": []}
        out: dict = {"last": head, "messages": [dict(m) for m in _HELD if m["n"] > after]}
        answers = [dict(a) for a in _ANSWERS if a["n"] > after]
        if answers:
            out["answers"] = answers
        return out


def _text(parts: list[dict]) -> str:
    words = " ".join(str(p.get("text") or "") for p in parts or []
                     if p.get("type") in ("text", "media")).strip()
    if len(words) > 2 and words.startswith("_") and words.endswith("_"):
        words = words[1:-1]               # "_Photo_": a kind, not emphasis
    if len(words) > TEXT_MAX:
        words = words[:TEXT_MAX - 1].rstrip() + "…"
    return words


#: Words of the message reacted to, quoted in a reaction's text.
QUOTE_MAX = 80


def _reacted(key: str, target: dict | None) -> str:
    """A reaction's words: `Reacted 👍 to "see you at 6"`, or just
    `Reacted 👍` when the message is not held."""
    head = f"Reacted {key}".rstrip() if key else "Reacted"
    words = _text(target["parts"]) if target else ""
    if len(words) > QUOTE_MAX:
        words = words[:QUOTE_MAX - 1].rstrip() + "…"
    return f"{head} to \u201c{words}\u201d" if words else head


def on_event(r, ev: dict, bridges: set[str], now: float | None = None) -> dict | None:
    """A sync event in room `r` (matrix.Room), after the room and
    archive_wake have taken it. Returns the notice kept, or None. Never
    raises: a notice is not worth the sync loop."""
    try:
        from . import archive, archive_wake, quiet

        reaction = archive_wake._reacting(ev, r.owner, bridges)
        if not reaction and not archive_wake._from_someone(ev, r.owner, bridges):
            return None
        ev_id = str(ev.get("event_id") or "")
        now = time.time() if now is None else now
        at = (ev.get("origin_server_ts") or 0) / 1000
        if not ev_id or not at or at < now - FRESH_S:
            return None
        with _LOCK:
            if ev_id in _TOLD:
                return None
        hush = quiet.quiet_of(r.thread, now) is not None
        if not hush and r.thread in archive.archived():
            return None
        sender = str(ev.get("sender") or "")
        with r.lock:
            if reaction:
                rel = (ev.get("content") or {}).get("m.relates_to") or {}
                key, to = str(rel.get("key") or ""), str(rel.get("event_id") or "")
                target = r._by_id.get(to)
                if target is not None and target.get("role") != "user":
                    return None            # someone else's message reacted to
                text = _reacted(key, target)
            else:
                m = r._by_id.get(ev_id)
                text = _text(m["parts"]) if m else str((ev.get("content") or {}).get("body") or "")[:TEXT_MAX]
            name = r.members.get(sender) or sender
            mxc = r.avatars.get(sender) or ""
            title = r.title()
            me = {r.owner, os.environ.get("MATRIX_SAM_ID") or ""}
            others = [u for u in r.members if u not in me and u not in bridges]
        out = {"thread": r.thread, "room": r.id, "title": title,
               "group": len(others) > 1, "id": ev_id,
               "sender": {"id": sender, "name": name},
               "text": text, "at": round(at, 3)}
        if reaction:
            out["reaction"] = {"key": key, "to": to}
        chips = _ready(r.thread, ev_id)
        if chips:
            out["suggestions"] = chips
        if mxc:
            # The sender's picture (matrix.Room._sender_extra's): their
            # profile photo through the bridge.
            from . import matrix

            out["avatar"] = {"url": f"/rooms/{r.thread}/avatar?user={matrix._q(sender)}",
                             "etag": matrix._etag(mxc)}
        with _LOCK:
            if ev_id in _TOLD:
                return None
            _TOLD.append(ev_id)
            if hush:
                before = _QUIET.get(r.thread)
                _QUIET[r.thread] = {**out, "held": (before or {}).get("held", 0) + 1}
                return None
            out = _keep(out)
        from . import session_events

        session_events.poke()
        return out
    except Exception as e:  # noqa: BLE001
        log.warning("room notices: %s not kept: %s", getattr(r, "thread", "?"), e)
        return None


def _keep(m: dict) -> dict:
    """Number `m` and keep it. Under _LOCK."""
    _SEQ[0] += 1
    m = {"n": _SEQ[0], **m}
    _HELD.append(m)
    return m


def _ready(thread: str, key: str) -> list[dict]:
    """The one-tap answers room_replies already has for message `key`."""
    try:
        from . import room_replies

        return room_replies.ready(thread, key)
    except Exception:  # noqa: BLE001 — a notice without chips still goes
        return []


def answers_ready(thread: str, key: str, chips: list[dict]) -> bool:
    """room_replies has `chips` for message `key` of `thread`: when that
    message was told, they go to the phones as an `answers` entry; when it
    is held while the chat is quiet, they ride on it when it is told.
    Returns whether anything was kept. Never raises."""
    if not chips or not thread or not key:
        return False
    try:
        with _LOCK:
            held = _QUIET.get(thread)
            if held is not None and held.get("id") == key:
                held["suggestions"] = list(chips)
                return True
            if not any(m["thread"] == thread and m["id"] == key for m in _HELD):
                return False
            _SEQ[0] += 1
            _ANSWERS.append({"n": _SEQ[0], "thread": thread, "id": key,
                             "suggestions": list(chips)})
        from . import session_events

        session_events.poke()
        return True
    except Exception as e:  # noqa: BLE001
        log.warning("room notices: answers for %s not kept: %s", thread[:8], e)
        return False


def release_held(now: float | None = None) -> int:
    """Tell what came while a chat was quiet, now that it is not: its
    newest message, `held` the count, unless the chat is archived by then.
    Returns how many were told. Never raises."""
    if not _QUIET:
        return 0
    try:
        from . import archive, quiet

        now = time.time() if now is None else now
        with _LOCK:
            waiting = list(_QUIET)
        done = [t for t in waiting if quiet.quiet_of(t, now) is None]
        if not done:
            return 0
        shelved = archive.archived()
        told = 0
        with _LOCK:
            for t in done:
                m = _QUIET.pop(t, None)
                if m is not None and t not in shelved:
                    if not m.get("suggestions"):
                        chips = _ready(t, m.get("id") or "")
                        if chips:
                            m["suggestions"] = chips
                    _keep(m)
                    told += 1
        return told
    except Exception as e:  # noqa: BLE001
        log.warning("room notices: held messages not released: %s", e)
        return 0


def _reset_for_tests() -> None:
    with _LOCK:
        _QUIET.clear()
        _HELD.clear()
        _ANSWERS.clear()
        _TOLD.clear()
        _SEQ[0] = int(time.time() * 1000)
