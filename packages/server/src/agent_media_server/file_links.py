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
(`state_dir()/shared-links.json`), so re-reading a thread does not copy again;
a changed file is copied anew.
"""

from __future__ import annotations

import json
import logging
import os
import re
import stat
import threading
from pathlib import Path
from urllib.parse import unquote

log = logging.getLogger("agent-media.server.file_links")

IMAGE = {"png", "jpg", "jpeg", "gif", "webp", "avif", "heic", "heif", "bmp"}
VIDEO = {"mp4", "m4v", "webm", "mov"}
AUDIO = {"mp3", "wav", "ogg", "opus", "m4a", "aac", "flac"}
#: The kinds a bare path is linked for.
EXTS = IMAGE | VIDEO | AUDIO | {"pdf"}

#: Hidden folders whose files are safe to show: where browser tools
#: (Playwright MCP) and preview scripts drop their screenshots.
ALLOWED_HIDDEN = {".playwright-mcp", ".local-previews"}
#: Never, even if ALLOWED_HIDDEN grows (a name with "secret" in it neither).
DENIED_PARTS = {".ssh", ".gnupg", ".password-store", ".aws", ".kube", ".docker"}
DENIED_ROOTS = (Path("/proc"), Path("/sys"), Path("/dev"), Path("/run"))
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


def max_bytes() -> int:
    def mb(name: str, default: int) -> int:
        try:
            return max(1, int(os.environ.get(name) or default))
        except ValueError:
            return default
    return min(mb("MEDIA_SHARED_LINK_MAX_MB", 100), mb("MEDIA_UPLOAD_MAX_MB", 512)) << 20


def _denied_parts(p: Path) -> bool:
    for part in p.parts[1:]:
        low = part.lower()
        if "secret" in low or low in DENIED_PARTS:
            return True
        if part.startswith(".") and part not in ALLOWED_HIDDEN:
            return True
    return False


def eligible(raw: str, any_kind: bool = False) -> Path | None:
    """The real file `raw` names when it may be shown, else None.
    `any_kind`: a `Shared file:` line, which shares on purpose, so any
    extension; a bare path must be one of `EXTS`."""
    raw = raw.strip()
    if raw.startswith("file://"):
        raw = raw[len("file://"):]
    if not raw or "\x00" in raw:
        return None
    p = Path(os.path.expanduser(raw))
    if not p.is_absolute():
        return None
    if not p.exists() and "%" in raw:
        p = Path(os.path.expanduser(unquote(raw)))
    if not any_kind and p.suffix.lower().lstrip(".") not in EXTS:
        return None
    try:
        real = p.resolve(strict=True)
        st = os.stat(real)
    except (OSError, RuntimeError, ValueError):
        return None
    if _denied_parts(p) or _denied_parts(real):
        return None
    if any(real.is_relative_to(d) for d in DENIED_ROOTS):
        return None
    if not stat.S_ISREG(st.st_mode) or st.st_size <= 0 or st.st_size > max_bytes():
        return None
    if not os.access(real, os.R_OK):
        return None
    return real


# ---- the copies, remembered ----------------------------------------------

_LOCK = threading.Lock()
_cache: dict[str, str] | None = None
CACHE_MAX = 2000


def _cache_path() -> Path:
    from agent_media_core import _paths

    return _paths.state_dir() / "shared-links.json"


def _load() -> dict[str, str]:
    global _cache
    if _cache is None:
        try:
            got = json.loads(_cache_path().read_text())
            _cache = got if isinstance(got, dict) else {}
        except (OSError, ValueError):
            _cache = {}
    return _cache


def _save(cache: dict[str, str]) -> None:
    path = _cache_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if len(cache) > CACHE_MAX:
            for k in list(cache)[: len(cache) - CACHE_MAX]:
                del cache[k]
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(cache))
        os.replace(tmp, path)
    except OSError as e:
        log.warning("shared links: could not save %s: %s", path, e)


def shared_copy(real: Path) -> Path | None:
    """Where the app can fetch `real` (an `eligible` file): itself under the
    upload root, else its copy there, made once per version of the file."""
    from agent_media_core import shared_files

    if shared_files.inside(real):
        return real
    try:
        st = real.stat()
    except OSError:
        return None
    key = f"{real}|{st.st_mtime_ns}|{st.st_size}"
    with _LOCK:
        cache = _load()
        done = cache.get(key)
        if done:
            d = Path(done)
            try:
                if d.is_file() and d.stat().st_size == st.st_size and shared_files.inside(d):
                    return d
            except OSError:
                pass
        try:
            dest = shared_files.share(real)
        except OSError as e:
            log.warning("shared links: could not copy %s: %s", real, e)
            return None
        cache.pop(key, None)
        cache[key] = str(dest)
        _save(cache)
        return dest


def reset_cache() -> None:
    """Forget the copies read so far (tests)."""
    global _cache
    with _LOCK:
        _cache = None


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
