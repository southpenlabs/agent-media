"""Replies to a message (server-contract.md §6.31).

David, 20 Oct 2026, from the list of what the bridged apps do: quote a
message when answering it, and see the quote on a reply that arrives. A
long press on a message offers Reply; the reply box shows what it answers;
`POST /reply` carries `reply_to` (the message's id) and `quote` (its words).

* **A Matrix room** (matrix.py `Room.send`): a real reply,
  `m.relates_to.m.in_reply_to`, so the bridge makes it WhatsApp's or
  Messenger's own reply. The others' replies come in from the sync the same
  way. Nothing is kept here.
* **An agent session**: the quote goes in front of the words as it always
  has (`send.compose` for a pane, `driver.headless.compose` for a headless
  session), and is kept here with the message's id, so that when the thread
  is read the quote comes off the bubble again and is `reply_to` instead
  (`mark`). Only a quote the app sent is lifted: a Markdown quote typed by
  hand stays in the words.

Each message that answers another carries `reply_to: {"id", "text"?,
"name"?, "mine"?}`. Stored in `<state_dir>/replies.json` as `{"<session>":
[{"id", "quote"}]}` (`_jsonmap.JsonMap`), a session's last `KEEP`.
"""

from __future__ import annotations

from ._jsonmap import JsonMap

STORE = JsonMap("replies.json")
KEEP = 100
#: How much of the message answered `reply_to.text` carries.
TEXT_MAX = 140


def short(words: str) -> str:
    w = " ".join((words or "").split())
    return w[:TEXT_MAX - 1].rstrip() + "…" if len(w) > TEXT_MAX else w


def record(session: str, mid: str, quote: str) -> None:
    """A message to `session` quoting message `mid`'s words `quote`."""
    if not session or not mid or not (quote or "").strip():
        return

    def fn(rows: dict) -> bool:
        got = rows.get(session) if isinstance(rows.get(session), list) else []
        got = [r for r in got if isinstance(r, dict) and r.get("quote") != quote]
        got.append({"id": mid, "quote": quote})
        rows[session] = got[-KEEP:]
        return True
    STORE.update(fn)


def _prefixes(quote: str) -> list[str]:
    """How the quote opens the message, as a pane and as a headless session
    are given it."""
    from . import send
    from .driver import headless

    return [send.compose("", quote).rstrip(), headless.compose("", quote).rstrip()]


def mark(session: str, msgs: list[dict]) -> None:
    """`reply_to` on the user messages that opened with a quote the app sent,
    and the quote off their words, in place. Run before the notes are taken
    off (retract.mark, reactions.mark): they come after the quote."""
    rows = STORE.get(session)
    if not isinstance(rows, list) or not rows:
        return
    for m in msgs:
        if m.get("role") != "user" or m.get("reply_to"):
            continue
        parts = m.get("parts") or []
        if not parts or parts[0].get("type") != "text":
            continue
        text = parts[0].get("text") or ""
        if not text.startswith(("Re: ", ">")):
            continue
        for r in reversed(rows):
            if not isinstance(r, dict) or not r.get("quote"):
                continue
            for p in _prefixes(str(r["quote"])):
                if text.startswith(p):
                    parts[0]["text"] = text[len(p):].lstrip()
                    m["reply_to"] = {"id": str(r.get("id") or ""), "text": short(r["quote"])}
                    break
            if m.get("reply_to"):
                break
