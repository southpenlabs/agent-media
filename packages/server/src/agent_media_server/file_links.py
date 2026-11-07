"""Files an agent names in its reply, as chips the app can open (§6.18).

David, 3 Nov 2026, after a reply named `/tmp/sasonica-chat-shots/ask-08-way-out.png`
as bare text: "that PNG link isn't clickable by the way. can we get any
relevant formats clickable from here". An agent could already show a file by
running `media attach` and putting the `Shared file:` line it prints in its
reply; one that just names the file left a path nobody could tap.

So when a thread's messages are read (`/conversation/log`, the thread stream),
each **assistant** reply's text is looked over — never the user's messages,
never a tool's output or the agent's reasoning — for absolute or `~/` paths,
bare, in backticks or as a markdown link's target, that name a file of a kind
the app can show (`EXTS`). Each is copied once into the upload root the way
`media attach` does (agent_media_core.shared_files.share) and a
`Shared file: <copy>` line is added after the words, so the app draws it as a
chip with no change of its own. A `Shared file:` line that points outside the
upload root (written by hand rather than printed by `media attach`) is
rewritten to a copy the same way. `GET /upload` still serves only the upload
root; nothing here widens it.

The transcript is untouched: this is only what the phone is shown. Speech
reads the reply from the transcript (the Stop hook), and skips `Shared file:`
lines anyway (intake/_text.py).

**Left alone**: a path with a hidden part (other than `ALLOWED_HIDDEN`, where
browser tools put their screenshots), anything under a folder whose name
has "secret" in it or under `~/.ssh`, `~/.gnupg` and the like (checked on the
path as written and as resolved, so a symlink does not get past), /proc,
/sys, /dev and /run, a file that is not a regular file, empty, unreadable or
over `max_bytes()` (MEDIA_SHARED_LINK_MAX_MB, 100 by default, never more than
MEDIA_UPLOAD_MAX_MB). Not linked from a bare path: html and svg (active
content the server would serve from its own origin), and text, markdown and
csv (replies name READMEs and notes all the time; a chip under each would
bury the ones that matter). `media attach` still shares any file on purpose.

Copies are remembered by source path, mtime and size
(`state_dir()/shared-links.json`, agent_media_core.shared_files.copy_once),
so re-reading a thread does not copy again; a changed file is copied anew. A
`Shared file:` line whose file is gone points at its newest remembered copy
(`last_copy`): the Claude Code Stop hook copies a reply's shared files as the
turn ends (intake/shared_check.py), so a temp folder deleted later does not
break the chip.
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path

from agent_media_core.shared_files import (  # noqa: F401 - the rules live in core
    ALLOWED_HIDDEN, AUDIO, DENIED_PARTS, DENIED_ROOTS, EXTS, IMAGE, VIDEO,
    _denied_parts, eligible, max_bytes)

log = logging.getLogger("agent-media.server.file_links")

#: At most this many chips added to one reply.
MAX_PER_MESSAGE = 8

_EXT_ALT = "|".join(sorted(EXTS, key=len, reverse=True))
# A path with no spaces: starts at `/`, `~/` or `file://`, not inside a URL or
# a longer word, and ends at a known extension (not `a.png.bak`).
_BARE = re.compile(
    r"(?<![\w/:.~%\-])((?:file://)?(?:~/|/)[^\s`'\"<>()\[\]{}|*]*?\.(?:" + _EXT_ALT + r"))"
    r"(?![\w/\-]|\.\w)", re.IGNORECASE)
# In backticks, where a path may have spaces.
_TICKED = re.compile(r"`((?:file://)?(?:~/|/)[^`\n]*?\.(?:" + _EXT_ALT + r"))`", re.IGNORECASE)
_LINE = re.compile(r"^Shared file: (\S.*?)\s*$")


# ---- the copies, remembered (agent_media_core.shared_files) ---------------

def shared_copy(real: Path) -> Path | None:
    """Where the app can fetch `real` (an `eligible` file): itself under the
    upload root, else its copy there, made once per version of the file."""
    from agent_media_core import shared_files

    return shared_files.copy_once(real)


def reset_cache() -> None:
    """Forget the copies read so far (tests)."""
    from agent_media_core import shared_files

    shared_files.reset_cache()


# ---- the replies ----------------------------------------------------------

def _sig(p: Path) -> tuple[int, int] | None:
    try:
        st = p.stat()
    except OSError:
        return None
    return st.st_size, st.st_mtime_ns


def link_text(text: str) -> str:
    """`text` with a `Shared file:` line for each file it names, and its
    `Shared file:` lines that point outside the upload root pointed at a copy."""
    if not text or ("/" not in text):
        return text
    from agent_media_core import shared_files

    lines = text.split("\n")
    shared: set[str] = set()          # the paths already carried as lines
    sigs: set[tuple[int, int]] = set()  # … and their size+mtime: a `media attach` copy keeps the original's
    scan: list[str] = []
    for i, line in enumerate(lines):
        m = _LINE.match(line)
        if not m:
            scan.append(line)
            continue
        path = m.group(1)
        p = Path(os.path.expanduser(path))
        if not shared_files.inside(p):
            real = eligible(path, any_kind=True)
            dest = shared_copy(real) if real else None
            if dest is None and not p.exists():
                # Gone since it was copied (a temp folder removed): its copy.
                dest = shared_files.last_copy(path)
            if dest is None:
                shared.add(path)
                continue
            lines[i] = f"Shared file: {dest}"
            p = dest
        shared.add(str(p.resolve()) if p.exists() else str(p))
        sig = _sig(p)
        if sig:
            sigs.add(sig)
    body = "\n".join(scan)
    found: list[tuple[int, str]] = []
    for rx in (_TICKED, _BARE):
        for m in rx.finditer(body):
            found.append((m.start(1), m.group(1)))
    added: list[str] = []
    seen: set[str] = set()
    for _pos, raw in sorted(found):
        if len(added) >= MAX_PER_MESSAGE:
            break
        real = eligible(raw)
        if real is None or str(real) in seen:
            continue
        seen.add(str(real))
        sig = _sig(real)
        if str(real) in shared or (sig and sig in sigs):
            continue
        dest = shared_copy(real)
        if dest is None or str(dest) in shared:
            continue
        shared.add(str(dest))
        if sig:
            sigs.add(sig)
        added.append(f"Shared file: {dest}")
    out = "\n".join(lines)
    if added:
        out = out.rstrip() + "\n\n" + "\n".join(added)
    return out


def mark(msgs: list[dict]) -> None:
    """Link the files the assistant's replies name, in place (see the module
    note). The text parts of assistant messages only."""
    for m in msgs:
        if m.get("role") != "assistant" or m.get("compacted"):
            continue
        for part in m.get("parts") or []:
            if part.get("type") != "text":
                continue
            text = part.get("text") or ""
            try:
                new = link_text(text)
            except Exception:  # noqa: BLE001 - a chip is never worth a thread
                log.exception("shared links: reading a reply failed")
                continue
            if new != text:
                part["text"] = new
