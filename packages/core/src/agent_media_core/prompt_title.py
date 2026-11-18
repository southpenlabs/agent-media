"""A chat's first message, made fit to be its name.

A chat nobody has named is called by the first thing said in it. When that
was agent mail, the first thing said is the delivery's wrapper —
`[From Agent-media (thread 1e9f…)]`, `[agent-mail #1038, thread #1038]
Stored message from … Read on the mailbox host with: …` — and the chats list
showed eight lines of it (David, 18 Nov 2026: "yeah both": drop the header,
and name such chats).

`clean(text)` strips the wrappers. A pointer to stored mail is named by that
mail's subject, read from the agent-mail database; a mail delivered in full is
named by its first sentence, which is how mail between sessions is written
(subject first). Shared-file paths are taken out of any title: a path is noise
in a list.

Headless chats get no `ai-title` from Claude Code, so without this the
wrapper was their name for good.
"""

from __future__ import annotations

import os
import re
import sqlite3
import threading
from pathlib import Path

#: A leading `[From …]`, `[agent-mail #…]` or `[reply — …]` tag. Brackets may
#: nest (`[reply — [From X] Subject…]`), so these are matched by a scanner.
_TAG_HEADS = ("[from ", "[agent-mail #", "[reply — ", "[reply - ")

#: agent-mail's pointer to stored mail (bin/agent-mail, the relay text).
_STORED = re.compile(
    r"^Stored message from (?P<sender>[\w.-]+) for (?P<recipient>[\w.-]+)"
    r"(?:, meant for chat \S+)?\.\s+Read on the mailbox host with:\s+(?P<cmd>.*?)\s+\.(?:\s|$)",
    re.S)
#: The older pointer: `New mail from A for B: <subject>. Read it with: …`.
_NEW_MAIL = re.compile(r"^New mail from [\w.-]+ for [\w.-]+:\s+(?P<subject>.+?)\.?\s+Read it with:", re.S)
#: A handed-on mail: `Agent mail #924 from X (…): <words>`.
_HANDED = re.compile(r"^Agent mail #\d+ from [\w.-]+(?: \([^)]*\))?:\s+", re.S)

_PATH = re.compile(
    r"(?:(?:screenshot|shared file|file|image|photo)s?:\s*)?"
    r"(?<![\w/.~…])(?:~|…|\.\.\.)?(?:/[\w.@+%-]+){2,}\.[A-Za-z0-9]{2,5}\b",
    re.I)


def _tag_end(text: str) -> int:
    """Where a leading bracketed tag ends (past its `]`), or 0 for none."""
    if not text.lower().startswith(_TAG_HEADS):
        return 0
    depth = 0
    for i, ch in enumerate(text):
        if ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
            if depth == 0:
                return i + 1
    return 0


def _strip_tags(text: str) -> tuple[str, str, bool]:
    """`(rest, reply title, any stripped)`: the leading tags taken off."""
    stripped, reply = False, ""
    while True:
        text = text.lstrip()
        end = _tag_end(text)
        if not end:
            return text, reply, stripped
        tag = text[:end]
        if tag.lower().startswith(("[reply — ", "[reply - ")):
            reply = tag[len("[reply — "):-1]
        text, stripped = text[end:], True


def _without_paths(text: str) -> str:
    text = _PATH.sub("", text)
    # What a removed path leaves behind: "()", "( )", "(: ", " :", "  ".
    text = re.sub(r"\(\s*[:,;]?\s*\)", "", text)
    text = re.sub(r"\(\s*[:,;]\s*", "(", text)
    text = re.sub(r"\s+([:,;.)])", r"\1", text)
    return " ".join(text.split())


def _first_sentence(text: str) -> str:
    """Up to the first line break or sentence end outside brackets/quotes."""
    text = text.strip()
    line = text.split("\n", 1)[0].strip()
    depth, quote = 0, ""
    for i, ch in enumerate(line):
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth = max(0, depth - 1)
        elif ch in "\"“”" and not quote:
            quote = "”" if ch == "“" else ch
        elif quote and ch == quote:
            quote = ""
        elif ch in ".!?" and not depth and not quote:
            after = line[i + 1:i + 3]
            if after[:1] == " " and after[1:2].isupper() and not _abbrev(line[:i]):
                return line[:i + (ch != ".")].strip()
    return line.rstrip(".")


def _abbrev(before: str) -> bool:
    word = before.rsplit(" ", 1)[-1].lower()
    return word in {"e.g", "i.e", "etc", "vs", "cf", "no", "st", "mr", "ms", "dr"} or len(word) == 1


_SUBJECTS: dict[tuple[str, int], str] = {}
_SUBJECTS_LOCK = threading.Lock()


def _db_of(cmd: str) -> str:
    m = re.search(r"--db\s+(\S+)", cmd)
    if m:
        return m.group(1).strip("'\"")
    state = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local/state")
    return os.environ.get("AGENT_MAIL_DB") or str(state / "agent-mail/mail.sqlite3")


def mail_subject(cmd: str) -> str:
    """The subject of the stored mail a pointer's read command names, read
    straight from agent-mail's database (read-only). "" when it cannot be."""
    m = re.search(r"\bshow\s+(\d+)\b", cmd)
    if not m:
        return ""
    db, ident = _db_of(cmd), int(m.group(1))
    with _SUBJECTS_LOCK:
        if (db, ident) in _SUBJECTS:
            return _SUBJECTS[(db, ident)]
    subject = ""
    if os.path.exists(db):
        try:
            con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=1.0)
            try:
                row = con.execute("SELECT subject FROM messages WHERE id = ?", (ident,)).fetchone()
            finally:
                con.close()
            subject = " ".join(str(row[0] or "").split()) if row else ""
        except sqlite3.Error:
            return ""      # not kept: a busy database answers next time
    with _SUBJECTS_LOCK:
        _SUBJECTS[(db, ident)] = subject
    return subject


def clean(text: str) -> str:
    """`text` as a chat's name: mail wrappers and file paths taken out (see
    the module). Text that is neither comes back with whitespace collapsed."""
    text = str(text or "")
    rest, reply, mail = _strip_tags(text)
    if mail:
        m = _STORED.match(rest)
        if m:
            subject = mail_subject(m.group("cmd"))
            return _without_paths(subject) or f"Mail from {m.group('sender')}"
        m = _NEW_MAIL.match(rest)
        if m:
            return _without_paths(m.group("subject"))
        if reply:
            return clean(reply)
        rest = _HANDED.sub("", rest, count=1)
        named = _first_sentence(_without_paths_keep_lines(rest))
        if named:
            return named
    return _without_paths(text) or " ".join(text.split())


def _without_paths_keep_lines(text: str) -> str:
    return "\n".join(_without_paths(line) for line in text.split("\n"))
