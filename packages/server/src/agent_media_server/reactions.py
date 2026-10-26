"""`POST /session/react` — an emoji on a message (server-contract.md §6.27).

David, 26 Oct 2026: "Can we have reactions to messages in chats?" — both
kinds of chat. A long press on a message picks an emoji; one reaction of
yours per message, as WhatsApp and Messenger have it: another emoji replaces
it, `key: ""` takes it off (the app sends that for a tap on the one you
put there).

* **A Matrix room** (matrix.py `Room.react`): sent as the owner, a real
  `m.reaction`, so the bridge carries it to WhatsApp or Messenger, and the
  others' reactions come back on the sync. Nothing is kept here.
* **An agent session**: kept here, and the **next** message sent to the
  session opens with a sentence saying so (`NOTE`, the way a retraction's
  note does, retract.py), so a thumbs-up costs no turn of its own. The
  sentence is taken off the bubble again when the thread is read.

Each message carries `reactions: [{"key", "count", "mine", "by"?}]` in
`/conversation/log` and the thread stream (`mark`); absent when it has none.

Stored in `<state_dir>/reactions.json` as `{"<session>": {"items":
{"<message id>": "<key>"}, "notes": [{"id", "key", "quote"}]}}`
(`_jsonmap.JsonMap`); a session keeps its last `KEEP` items.
"""

from __future__ import annotations

import re

from . import auth, driver, sessions
from ._jsonmap import JsonMap

STORE = JsonMap("reactions.json")
KEEP = 200
#: How much of the message the note quotes.
QUOTE_MAX = 80
#: An emoji is a few code points (a family, a flag, a skin tone); nothing longer.
KEY_MAX = 32

_NOTE_RE = re.compile(r"^(?:\(I reacted [^()]{1,40} to (?:your|my) message(?:, “[^”]*”)?\.\)\s*)+")


def _quote(words: str) -> str:
    w = " ".join((words or "").replace("“", '"').replace("”", '"').split())
    return w[:QUOTE_MAX - 1].rstrip() + "…" if len(w) > QUOTE_MAX else w


def note(key: str, quote: str, role: str = "assistant") -> str:
    whose = "my" if role == "user" else "your"
    q = _quote(quote)
    return f"(I reacted {key} to {whose} message, “{q}”.)" if q \
        else f"(I reacted {key} to {whose} message.)"


def strip_note(text: str) -> str:
    """The words of a message without the reaction notes before them."""
    return _NOTE_RE.sub("", text, count=1) if text.startswith("(I reacted") else text


def take_notes(session: str) -> str:
    """The notes owed to `session`'s next message, once: "" when none are."""
    row = STORE.get(session)
    if not isinstance(row, dict) or not row.get("notes"):
        return ""
    owed = list(row["notes"])

    def fn(rows: dict) -> bool:
        r = rows.get(session)
        if not isinstance(r, dict) or not r.get("notes"):
            return False
        r.pop("notes", None)
        return True
    STORE.update(fn)
    return " ".join(note(str(n.get("key") or ""), str(n.get("quote") or ""),
                         str(n.get("role") or "assistant"))
                    for n in owed if isinstance(n, dict) and n.get("key"))


def with_notes(session: str, text: str) -> str:
    """`text` as the next message should say it: the owed notes, then the words."""
    owed = take_notes(session)
    return f"{owed}\n\n{text}" if owed else text


def mark(session: str, msgs: list[dict]) -> None:
    """`reactions` on the messages reacted to, in place; and the notes off
    any user message that carried them."""
    for m in msgs:
        if m.get("role") != "user":
            continue
        parts = m.get("parts") or []
        if parts and parts[0].get("type") == "text" and \
                (parts[0].get("text") or "").startswith("(I reacted"):
            parts[0]["text"] = strip_note(parts[0]["text"])
    row = STORE.get(session)
    items = row.get("items") if isinstance(row, dict) else None
    if not items:
        return
    for m in msgs:
        key = items.get(m.get("id"))
        if key:
            m["reactions"] = [{"key": key, "count": 1, "mine": True}]


def _record(session: str, mid: str, key: str, quote: str, role: str) -> None:
    def fn(rows: dict) -> bool:
        r = rows.get(session) if isinstance(rows.get(session), dict) else {}
        items = dict(r.get("items") or {})
        if items.get(mid, "") == key:
            return False
        items.pop(mid, None)
        if key:
            items[mid] = key
        r["items"] = dict(list(items.items())[-KEEP:])
        # A reaction changed before it was told replaces the one owed; one
        # taken off before then is never told.
        notes = [n for n in (r.get("notes") or [])
                 if isinstance(n, dict) and n.get("id") != mid]
        if key:
            notes.append({"id": mid, "key": key, "quote": quote, "role": role})
        if notes:
            r["notes"] = notes[-10:]
        else:
            r.pop("notes", None)
        rows[session] = r
        return True
    STORE.update(fn)


def valid_key(key: str) -> bool:
    """An emoji, not words: short, no letters, digits or spaces of its own
    (a keycap's digit comes with its combining mark)."""
    if not key or len(key) > KEY_MAX or any(c.isspace() for c in key):
        return False
    return not any(c.isascii() and c.isalnum() for c in key) or "⃣" in key


def session_react(session: str, mid: str, key: str, bearer: str, *,
                  text: str = "", role: str = "") -> tuple[bool, dict]:
    """`{"session", "id", "key", "text"?, "role"?}` → `{"session", "id",
    "reactions"}`. `key` is the emoji, `""` to take yours off; `text` the
    message's words, which the agent's note quotes; `role` whose message it
    is (`"user"` for your own)."""
    session = (session or "").strip()
    if not sessions._SESSION.fullmatch(session):
        return False, {"error": "not a session id", "status": 400}
    mid = (mid or "").strip()
    if not mid:
        return False, {"error": "react needs the message's id", "status": 400}
    key = (key or "").strip()
    if key and not valid_key(key):
        return False, {"error": "a reaction is one emoji", "status": 400}
    user, err = auth.gate(bearer)
    if not user:
        return False, err
    from . import matrix

    room = matrix.room(session)
    if room is not None:
        ok, detail = room.react(mid, key)
        if not ok:
            return False, detail
        return True, {"session": session, "id": mid, "reactions": detail["reactions"]}
    if not (sessions.live_sessions().get(session) or sessions.session_exists(session)
            or driver.owned_headless(session)):
        return False, {"error": f"no such session {session[:8]}", "status": 404}
    _record(session, mid, key, text, "user" if role == "user" else "assistant")
    return True, {"session": session, "id": mid,
                  "reactions": [{"key": key, "count": 1, "mine": True}] if key else []}
