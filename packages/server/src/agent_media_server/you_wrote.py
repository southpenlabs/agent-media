"""Where the listener has written, for the thread list's Show → Sent and
Waiting for reply (David, 30 Oct 2026: "like Gmail's Sent label … with a
Waiting for reply option").

Every `/targets` row gets two fields (§6.1):

- `you_at`: when the listener last wrote in the thread (epoch s, the
  message's own time), else None — never, or nothing readable.
- `last_from_you`: whether that message is still unanswered: nothing with
  words has come back after it. For an agent's thread that is a reply whose
  turn is over, so a thread whose agent is working on the message right now
  is still waiting (it is not answered yet); a turn cut off before it said
  anything leaves it waiting too. For a person's chat (a Matrix room) it is
  anyone else's message after it, and the same for a quick chat's model.

What is not the listener writing, though it sits on the user side of a
transcript: another session's message (`<cross-session-message>`, the
Builder's `peer`), another agent's mail and the session host's notes
(`[From …]`), an interrupt, a `!` shell command (it is answered by its
output, never by the agent), and everything `transcript.Builder` already
leaves out (hook output, reminders, settings commands, tool results).

**Cost.** Hundreds of rows, polled often, so no transcript is read whole per
call. Per file the answer is kept with its inode, size and mtime: an
unchanged file costs the stat `/targets` already makes. A file seen for the
first time is scanned backwards for its newest prompt that is the
listener's (only lines that could be a prompt are parsed) and folded from
there on; a file that grew is folded over its new bytes only, by a builder
kept per file and trimmed to the messages after the listener's last one. A
file with no prompt of the listener's in it is scanned to its start once,
then only as it grows. Gemini's and Grok's files are folded whole by
`transcript` (their own cache, until the file changes), opencode's database
read whole when its parts change; Hermes keeps no file and has neither
field. Matrix rooms and quick chats answer from the messages they already
hold (matrix.py, quick.py).
"""
from __future__ import annotations

import os
import re
import threading

#: User-side words that are not the listener: agent mail and the session
#: host's notes (`[From …]`), an interrupt, another session's raw message, a
#: subagent's hand-back or a task's notice (`<agent-message`,
#: `<task-notification`, a system reminder or notification), and a job run
#: on a timer, whose prompt opens with `[Scheduled: <what>]` (David, 30 Oct
#: 2026: "leave out the ones not sent by me").
_NOT_YOU = re.compile(
    r"^(\[(From |Request interrupted|Scheduled[:\]]|SYSTEM NOTIFICATION)"
    r"|<(cross-session-message|agent-message|task-notification|system-reminder)\b)")


def yours(msg: dict) -> bool:
    """A transcript message (`transcript.Builder`'s shape) the listener wrote."""
    if msg.get("role") != "user" or msg.get("peer") or msg.get("shell"):
        return False
    parts = msg.get("parts") or []
    text = str((parts[0].get("text") if parts else "") or "").strip()
    return bool(text) and not _NOT_YOU.match(text)


def answered(msg: dict) -> bool:
    """An agent's message that answers: words in it, and its turn over."""
    if msg.get("role") != "assistant" or msg.get("compacted") \
            or (msg.get("turn") or {}).get("running"):
        return False
    return any(p.get("type") == "text" and str(p.get("text") or "").strip()
               for p in msg.get("parts") or [])


def of_messages(msgs: list[dict]) -> tuple[float | None, bool]:
    """`(you_at, last_from_you)` for a whole conversation's messages."""
    for i in range(len(msgs) - 1, -1, -1):
        if yours(msgs[i]):
            return (round(float(msgs[i].get("at") or 0), 3) or None,
                    not any(answered(m) for m in msgs[i + 1:]))
    return None, False


def of_chat(msgs: list[dict]) -> tuple[float | None, bool]:
    """`(you_at, last_from_you)` for a chat whose `role: "user"` messages
    are the listener's and every other one someone answering (a Matrix
    room, a quick chat): waiting while the newest message is the listener's."""
    for i in range(len(msgs) - 1, -1, -1):
        if msgs[i].get("role") == "user":
            return round(float(msgs[i].get("at") or 0), 3) or None, i == len(msgs) - 1
    return None, False


class _File:
    __slots__ = ("sig", "offset", "seam", "builder", "you_at", "from_you", "lock")

    def __init__(self) -> None:
        self.sig: tuple = ()
        self.offset = 0
        self.seam = b""
        self.builder = None
        self.you_at: float | None = None
        self.from_you = False
        self.lock = threading.Lock()


#: `{path: _File}`, one per transcript a row has asked about. Small: the
#: answer, a seam and the few messages after the listener's last one.
_FILES: dict[str, _File] = {}
#: `{key: (sig, answer)}` for the conversations folded whole.
_WHOLE: dict[str, tuple[tuple, tuple[float | None, bool]]] = {}
_LOCK = threading.Lock()


def _reset_for_tests() -> None:
    with _LOCK:
        _FILES.clear()
        _WHOLE.clear()


def _start(fh, end: int, reader) -> int:
    """The offset of the newest prompt the listener wrote, or 0 when there
    is none: a backwards scan that parses only lines that could be a prompt."""
    from . import transcript as tr

    carry, pos = b"", end
    while pos > 0:
        n = min(tr._CHUNK, pos)
        pos -= n
        fh.seek(pos)
        buf = fh.read(n) + carry
        base = pos
        if pos > 0:
            cut = buf.find(b"\n")
            if cut < 0:
                carry = buf
                continue
            carry, buf, base = buf[:cut], buf[cut + 1:], pos + cut + 1
        else:
            carry = b""
        lines = buf.split(b"\n")
        starts, o = [], base
        for raw in lines:
            starts.append(o)
            o += len(raw) + 1
        for raw, at in zip(reversed(lines), reversed(starts)):
            if not reader.prompt_hint(raw):
                continue
            rec = tr._parse_line(raw, reader.wants)
            if rec is None or not reader.boundary(rec, False):
                continue
            one = reader.build(False)
            one.feed(rec)
            if one.messages and yours(one.messages[-1]):
                return at
    return 0


def _fold(f: _File) -> None:
    """Bring `f`'s answer up to date from its builder's messages, and trim
    them to what the next read needs."""
    msgs = f.builder.messages
    i = next((k for k in range(len(msgs) - 1, -1, -1) if yours(msgs[k])), -1)
    if i >= 0:
        f.you_at = round(float(msgs[i].get("at") or 0), 3) or f.you_at
        f.from_you = not any(answered(m) for m in msgs[i + 1:])
        if f.from_you:
            del msgs[:i]           # its message and what came after: still open
            return
    elif f.from_you:
        f.from_you = not any(answered(m) for m in msgs)
    del msgs[:-1]                  # the open message is all a later read needs


def _of_file(path: str, harness: str) -> tuple[float | None, bool]:
    from . import transcript as tr

    try:
        st = os.stat(path)
    except OSError:
        return None, False
    sig = (st.st_ino, st.st_size, st.st_mtime)
    with _LOCK:
        f = _FILES.get(path)
        if f is None:
            f = _FILES[path] = _File()
    with f.lock:
        if f.sig == sig:
            return f.you_at, f.from_you
        reader = tr.READERS.get(harness) or tr.READERS["claude"]
        try:
            with open(path, "rb") as fh:
                end = tr._complete_end(fh, st.st_size)
                appended = (f.builder is not None and f.sig and f.sig[0] == st.st_ino
                            and f.offset <= end and tr._seam(fh, f.offset) == f.seam)
                if not appended:
                    # First sight, or shrunk, replaced, rewritten: from the
                    # listener's newest prompt on.
                    f.builder, f.you_at, f.from_you = reader.build(False), None, False
                    f.offset = _start(fh, end, reader)
                tr._feed_range(f.builder, fh, f.offset, end, reader.wants)
                f.builder.settle()
                f.offset, f.seam = end, tr._seam(fh, end)
        except OSError:
            return None, False
        _fold(f)
        f.sig = sig
        return f.you_at, f.from_you


def _of_whole(key: str, sig, read) -> tuple[float | None, bool]:
    with _LOCK:
        got = _WHOLE.get(key)
    if got and got[0] == sig:
        return got[1]
    ans = of_messages(read() or [])
    with _LOCK:
        _WHOLE[key] = (sig, ans)
    return ans


def of_session(session: str, harness: str = "", path: str = "") -> tuple[float | None, bool]:
    """`(you_at, last_from_you)` for an agent's thread, from its transcript
    at `path` (the store's, when the caller has it) or the one found for it."""
    from . import transcript as tr

    if not path:
        if harness == "opencode":
            st = tr.file_state(session)
            if st is None:
                return None, False
            return _of_whole(session, st, lambda: tr._opencode_messages(session))
        harness, path = tr.transcript_of(session)
        if not path:
            return None, False
    if harness in tr._WHOLE:
        try:
            st = os.stat(path)
        except OSError:
            return None, False
        return _of_whole(path, (st.st_ino, st.st_size, st.st_mtime),
                         lambda: tr._WHOLE[harness](path))
    if harness not in tr.READERS:
        return None, False
    return _of_file(path, harness)
