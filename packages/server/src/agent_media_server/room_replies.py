"""One-tap answers in a chat with people (a Matrix room, §6.25).

David, 7 Nov 2026: "Can we have the one tap options for people chats too
please" (docs/proposals/2026-11-06-one-tap-answers.md). An agent's thread
gets its chips from the agent's `[[replies:]]` marker or the follow-up model
(sessions.suggestions_for); a room has neither, so when its newest message
is someone else's, up to three short answers in the owner's voice are asked
of a model, on a thread of their own, and kept per room keyed to that
message. The envelope offers them (`suggestions`, source `model`) once they
are there, and none once the owner has written since.

Private, always: a chat with a person is theirs as much as yours (tags.py),
so it is read only to MEDIA_PRIVATE_MODELS (intake/fast.py `private=True`),
and with none set there are no chips. MEDIA_ROOM_REPLIES=0 turns it off.

Kept in `<state_dir>/room-replies.json`: `{thread: {"key", "replies", "at"}}`,
`key` the message the answers are for.
"""

from __future__ import annotations

import logging
import os
import re
import threading
import time

log = logging.getLogger(__name__)

#: Messages of the chat a model reads, newest last.
CONTEXT = 12
#: Chips kept, and each one's length at most.
MAX = 3
CHARS = 80

PROMPT = (
    "You read the latest messages of a chat between the person you help and "
    "their contacts. The last message is to them. Write up to THREE short "
    "replies they might send back, most likely first, one per line, each "
    "different in what it says: in their own voice and the chat's language, "
    "as casual or formal as they write in it, a few words each (at most "
    "twelve). Emoji only if they use them. No numbering, no quotes, no names, "
    "no preamble. If nothing short fits (a question that needs real thought, "
    "a long message), reply: none"
)

_map = None
_busy: set[str] = set()
_lock = threading.Lock()


def _jsonmap():
    global _map
    if _map is None:
        from ._jsonmap import JsonMap

        _map = JsonMap("room-replies.json")
    return _map


def enabled() -> bool:
    return (os.environ.get("MEDIA_ROOM_REPLIES", "1") or "1").strip() != "0"


def _text(m: dict) -> str:
    return " ".join(p.get("text") or "" for p in m.get("parts") or []
                    if p.get("type") == "text").strip()


def _lines(out: str) -> list[str]:
    lines: list[str] = []
    for raw in (out or "").splitlines():
        line = re.sub(r"^\s*(?:\d+[.)]|[-•*])\s*", "", raw).strip().strip('"').strip("'").strip()
        if not line or line.lower().rstrip(".") == "none" or len(line) > CHARS:
            continue
        if line.lower() not in {x.lower() for x in lines}:
            lines.append(line)
    return lines[:MAX]


def _ask(thread: str, key: str, chat: str) -> None:
    try:
        from agent_media_core.intake import fast

        out = fast.chat(PROMPT, chat, 20, purpose_env=None, private=True) or ""
        _jsonmap().put(thread, {"key": key, "replies": _lines(out), "at": round(time.time(), 3)})
    except Exception as e:  # noqa: BLE001 — no chips is the cost
        log.warning("room replies %s: %s", thread[:8], e)
    finally:
        with _lock:
            _busy.discard(thread)


def chips(thread: str, messages: list[dict]) -> list[dict]:
    """The chips for a room's newest page (`messages`, oldest first):
    `[{"text", "source": "model"}]` when its newest message with words is
    someone else's and its answers are ready; otherwise [] (and, when none
    were asked for it yet, they are asked for now, in the background)."""
    if not enabled() or not thread:
        return []
    said = [m for m in messages if _text(m)]
    if not said or said[-1].get("role") == "user":
        return []
    last = said[-1]
    key = str(last.get("id") or "")
    if not key:
        return []
    have = _jsonmap().all().get(thread)
    if isinstance(have, dict) and have.get("key") == key:
        return [{"text": str(t), "source": "model"} for t in have.get("replies") or [] if t]
    from agent_media_core.intake import fast

    if not fast.models(private=True):
        return []
    with _lock:
        if thread in _busy:
            return []
        _busy.add(thread)
    chat = "\n".join(
        ("Them" if m.get("role") != "user" else "Me") + (
            f" ({(m.get('sender') or {}).get('name')})"
            if m.get("role") != "user" and (m.get("sender") or {}).get("name") else "")
        + ": " + _text(m)[:600] for m in said[-CONTEXT:])
    threading.Thread(target=_ask, args=(thread, key, chat), daemon=True,
                     name="room-replies").start()
    return []


def prefetch(r) -> None:
    """Someone's new message just came into room `r` (matrix.Room): ask for
    its answers now, in the background, so they are there when the chat is
    opened. Opened first, the chat drew its saved copy's chips for the
    message before, the first fresh read dropped them, and the new ones were
    ready ~20 s later — after David had looked away (7 Nov 2026: "why do
    those quick reply answers disappear?", in a chat with Steven). Never
    raises: the sync loop calls it."""
    try:
        messages, _older = r.page(CONTEXT, "")
        chips(r.thread, messages)
    except Exception as e:  # noqa: BLE001
        log.warning("room replies %s: prefetch: %s", getattr(r, "thread", "?")[:8], e)


def _reset_for_tests() -> None:
    global _map
    _map = None
    _busy.clear()
