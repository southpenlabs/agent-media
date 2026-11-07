"""The upload root, and putting a file in it (§6.18).

The app draws a `Shared file: <path>` line as a chip and fetches the file
through `GET /upload`, which serves only what is under the upload root
(MEDIA_UPLOAD_DIR, else ~/shared). `media attach` and the server's linking of
paths an agent names in a reply (agent_media_server/file_links.py) both put a
file there the same way: into today's folder, never over another, a file
already under the root left where it is.

Copies are remembered by source path, mtime and size
(`state_dir()/shared-links.json`, `copy_once`), so naming a file again does
not copy it again and a changed file is copied anew. `last_copy` finds the
newest copy of a path whose file is gone: a reply that shared a file from a
temporary folder still opens after the folder is deleted, as long as the
file was copied once while it was there (David, 8 Nov 2026, a chip that
read "Could not open the file" after its worktree was removed).
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import os
import shutil
import stat
import threading
from pathlib import Path
from urllib.parse import unquote

log = logging.getLogger("agent-media.shared_files")


# ---- what may be shown (agent_media_server/file_links.py has the why) -----

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


def root() -> Path:
    return Path(os.path.expanduser(os.environ.get("MEDIA_UPLOAD_DIR") or "~/shared"))


def inside(p: Path) -> bool:
    """Whether `p`, symlinks resolved, is under the upload root."""
    try:
        return p.resolve().is_relative_to(root().resolve())
    except (OSError, RuntimeError, ValueError):
        return False


def share(src: Path, today: dt.date | None = None) -> Path:
    """Where the app can fetch `src`: itself when already under the root,
    else a copy in today's folder (`shot.png`, then `shot-2.png`, …).
    `src` must be a regular file; OSError when the copy fails."""
    p = Path(src).expanduser()
    if inside(p):
        return p.resolve()
    folder = root() / (today or dt.date.today()).isoformat()
    folder.mkdir(parents=True, exist_ok=True)
    stem, ext = p.stem.lstrip(".") or "shared", p.suffix
    dest, n = folder / f"{stem}{ext}", 2
    while dest.exists():
        dest, n = folder / f"{stem}-{n}{ext}", n + 1
    shutil.copy2(p, dest)
    return dest


# ---- the copies, remembered ----------------------------------------------

_LOCK = threading.Lock()
_cache: dict[str, str] | None = None
CACHE_MAX = 2000


def _cache_path() -> Path:
    from . import _paths

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
        tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
        tmp.write_text(json.dumps(cache))
        os.replace(tmp, path)
    except OSError as e:
        log.warning("shared links: could not save %s: %s", path, e)


def _usable(d: Path, size: int | None = None) -> bool:
    try:
        return d.is_file() and inside(d) and (size is None or d.stat().st_size == size)
    except OSError:
        return False


def copy_once(real: Path) -> Path | None:
    """Where the app can fetch `real` (a readable regular file): itself under
    the upload root, else its copy there, made once per version of the file.
    None when it cannot be read or copied."""
    if inside(real):
        return real
    try:
        st = real.stat()
    except OSError:
        return None
    key = f"{real}|{st.st_mtime_ns}|{st.st_size}"
    with _LOCK:
        global _cache
        _cache = None                  # another process (the Stop hook) may have added
        cache = _load()
        done = cache.get(key)
        if done and _usable(Path(done), st.st_size):
            return Path(done)
        try:
            dest = share(real)
        except OSError as e:
            log.warning("shared links: could not copy %s: %s", real, e)
            return None
        cache.pop(key, None)
        cache[key] = str(dest)
        _save(cache)
        return dest


def last_copy(path: str | Path) -> Path | None:
    """The newest remembered copy of `path` (`~` expanded, symlinks in what
    is left of it resolved) that is still under the root, else None. For a
    file that is gone; one that exists goes through `copy_once`."""
    raw = os.path.abspath(os.path.expanduser(str(path)))
    want = {raw}
    try:
        want.add(str(Path(raw).resolve()))
    except (OSError, RuntimeError):
        pass
    with _LOCK:
        cache = _load()
        for key in reversed(list(cache)):
            src = key.rsplit("|", 2)[0]
            if src in want:
                d = Path(cache[key])
                if _usable(d):
                    return d
    return None


def reset_cache() -> None:
    """Forget the copies read so far (tests)."""
    global _cache
    with _LOCK:
        _cache = None
