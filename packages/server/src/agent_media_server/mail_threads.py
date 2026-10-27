"""Threads another agent's mail started, so the app can file them apart.

agent-mail-deliver (and `media session send`) opens a session for a letter
when its folder has none, and that session's first words are the letter's
pointer, `[From Cece] agent-mail #127 is for this session…`. In the thread
list it sat among David's own chats (David, 28 Oct 2026: "what's the best way
to make these messages not show up amongst regular chat messages?").

Each row gets `mail_from`: the sender's name ("Cece") when the thread began
with another agent's mail and nobody has written in it since but agents and
the session host, else None. The app files those under an "Agent mail"
section. Once David writes in one himself it is his chat like any other and
the mark goes for good. Archiving would not do: a reply clears that flag.

Only Claude transcripts are read (agent-mail starts Claude sessions). Each is
read once from the start and then only what was added since; a thread
decided either way (its first words were not mail, or David has written in
it) is never read again.
"""
from __future__ import annotations

import json
import re

#: Another agent's mail, as hook_claude_code reads it for the mail tone; the
#: session host's own notes are not mail.
_MAIL = re.compile(r"^\[From (?!the session host\])([^\]\n]{1,40})\]")
#: User lines that are not a person writing: the host's notes, an interrupt.
_NOT_A_PERSON = re.compile(r"^\[(From the session host|Request interrupted)")

#: session -> [transcript path, bytes read, sender or "" while undecided,
#: decided]. Decided rows are never read again.
_SEEN: dict[str, list] = {}


def _texts(line: str) -> str | None:
    """A transcript line's words when it is a user message, else None."""
    if '"user"' not in line:
        return None
    try:
        rec = json.loads(line)
    except ValueError:
        return None
    if rec.get("type") != "user" or rec.get("isMeta") or rec.get("isSidechain") \
            or rec.get("isCompactSummary"):
        return None
    content = (rec.get("message") or {}).get("content")
    if isinstance(content, list):
        content = " ".join(c.get("text", "") for c in content
                           if isinstance(c, dict) and c.get("type") == "text")
    text = " ".join(str(content or "").split())
    # Command wrappers, hook output and tool results are not anyone writing.
    if not text or text.startswith("<"):
        return None
    return text


def mail_from(session: str) -> str | None:
    """Who the thread's mail is from, while it is still a mail thread."""
    st = _SEEN.get(session)
    if st is None:
        from .sessions import _claude_files

        files = _claude_files(session)
        if not files:
            return None
        st = _SEEN[session] = [files[0], 0, "", False]
    path, offset, sender, decided = st
    if decided:
        return sender or None
    try:
        fh = open(path, "rb")
    except OSError:
        return None
    with fh:
        fh.seek(offset)
        # Line by line, so an ordinary chat stops at its first words.
        for raw in iter(fh.readline, b""):
            # Only whole lines: the last may still be being written.
            if not raw.endswith(b"\n"):
                break
            offset += len(raw)
            text = _texts(raw.decode("utf-8", errors="replace"))
            if text is None:
                continue
            m = _MAIL.match(text)
            if not sender:
                if not m:
                    st[1:] = [offset, "", True]
                    return None
                sender = m.group(1).strip()
            elif not m and not _NOT_A_PERSON.match(text):
                st[1:] = [offset, "", True]
                return None
    st[1:] = [offset, sender, False]
    return sender or None


def add_mail(rows: list[dict]) -> list[dict]:
    """Give each row `mail_from`, in place (see the module)."""
    for r in rows:
        sid = str(r.get("session") or "")
        r["mail_from"] = None
        if not sid or r.get("sealed") or (r.get("harness") or "claude") != "claude":
            continue
        try:
            r["mail_from"] = mail_from(sid)
        except Exception:  # noqa: BLE001 — a thread list never fails on this
            pass
    return rows
