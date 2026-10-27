"""A Matrix room's photos, videos, voice notes and files (server-contract.md §6.29).

David, 28 Oct 2026, of a "_sent a video_" line in a WhatsApp room: "Can we
make these clickable to prioritise download?" — and he chose photos shown,
the rest on a tap. A media message is a `media` part (matrix.py `_parts`),
and its bytes come through here, from the homeserver, only when asked for:

* `GET /rooms/<thread>/media/<event id>?thumb=1` — a photo's (or a video's
  poster's) thumbnail, at most `THUMB_PX` a side, kept once fetched. The
  app asks for these as the thread is drawn.
* `GET /rooms/<thread>/media/<event id>?ready=1` — fetch the whole of it
  now and say so: `{"ok", "size", "type"}`. A tap on a video, voice note or
  file asks this first, so the card can say it is downloading; the bridge
  fetches it from the network then (WhatsApp's and Telegram's media are
  fetched on demand), which is the "prioritise".
* `GET /rooms/<thread>/media/<event id>` — the whole of it, with `Range`,
  so a video seeks. Fetched first if it has not been.

Kept under `<state>/room-media/` by the mxc's hash (0600), the oldest
dropped past `KEEP_BYTES`. A fetch that failed is not tried again for
`RETRY_S` (the network no longer has it, or the phone is off).
"""

from __future__ import annotations

import hashlib
import logging
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

log = logging.getLogger("agent-media.server.room_media")

THUMB_PX = 480
#: The largest thing fetched whole.
MAX_BYTES = 256 * 1024 * 1024
#: The thumbnails' and downloads' total, at most; the least recently read go first.
KEEP_BYTES = 2 * 1024 * 1024 * 1024
RETRY_S = 600.0
TIMEOUT_S = 120.0
CHUNK = 256 * 1024

_LOCKS: dict[str, threading.Lock] = {}
_LOCKS_LOCK = threading.Lock()


def media_dir() -> Path:
    from . import contacts_photos

    return contacts_photos.photos_dir().parent / "room-media"


def _lock(name: str) -> threading.Lock:
    with _LOCKS_LOCK:
        return _LOCKS.setdefault(name, threading.Lock())


def _name(mxc: str, thumb: bool) -> str:
    return hashlib.sha256(mxc.encode()).hexdigest() + ("-t" if thumb else "")


def _failed_lately(d: Path, name: str) -> bool:
    p = d / f"{name}.none"
    try:
        return p.is_file() and time.time() - p.stat().st_mtime < RETRY_S
    except OSError:
        return False


def _tail(mxc: str) -> str:
    server, media = mxc[6:].split("/", 1)
    return f"{urllib.parse.quote(server, safe='')}/{urllib.parse.quote(media, safe='')}"


def _download(mxc: str, dest: Path) -> None:
    """The whole of `mxc` into `dest`, a chunk at a time: authenticated media
    with the owner's token, else the old media API."""
    from . import contacts_photos

    hs = contacts_photos._homeserver()
    if not hs:
        raise OSError("no MATRIX_HOMESERVER")
    tok = contacts_photos._token()
    tries = []
    if tok:
        tries.append((f"{hs}/_matrix/client/v1/media/download/{_tail(mxc)}", tok))
    tries.append((f"{hs}/_matrix/media/v3/download/{_tail(mxc)}", ""))
    last: Exception = OSError("no way to fetch it")
    for url, t in tries:
        tmp = dest.with_name(f".{dest.name}.part")
        try:
            req = urllib.request.Request(url, headers={"Authorization": f"Bearer {t}"} if t else {})
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            got = 0
            with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp, os.fdopen(fd, "wb") as f:
                while True:
                    chunk = resp.read(CHUNK)
                    if not chunk:
                        break
                    got += len(chunk)
                    if got > MAX_BYTES:
                        raise ValueError("too large")
                    f.write(chunk)
            os.replace(tmp, dest)
            return
        except (urllib.error.URLError, OSError, ValueError) as e:
            last = e
            try:
                tmp.unlink()
            except OSError:
                pass
    raise last


def _prune(d: Path) -> None:
    try:
        files = [(p.stat(), p) for p in d.iterdir() if p.is_file() and not p.name.startswith(".")]
    except OSError:
        return
    total = sum(st.st_size for st, _ in files)
    for st, p in sorted(files, key=lambda x: x[0].st_atime):
        if total <= KEEP_BYTES:
            break
        try:
            p.unlink()
            total -= st.st_size
        except OSError:
            pass


def fetch(info: dict, *, thumb: bool) -> Path | None:
    """The file for a media part's `info` (`{"mxc", "thumb_mxc"?, ...}`),
    fetched now if it has not been; None when it cannot be had."""
    from . import contacts_photos

    mxc = (info.get("thumb_mxc") or info["mxc"]) if thumb else info["mxc"]
    d = media_dir()
    name = _name(mxc, thumb)
    path = d / name
    with _lock(name):
        if path.is_file():
            try:
                os.utime(path)              # read now: kept longest
            except OSError:
                pass
            return path
        if _failed_lately(d, name):
            return None
        d.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(d, 0o700)
        except OSError:
            pass
        try:
            if thumb:
                data = contacts_photos.fetch_mxc(mxc, px=THUMB_PX, max_bytes=16 * 1024 * 1024)
                if contacts_photos.sniff(data) is None:
                    raise ValueError("not a picture")
                # The homeserver's thumbnail is used as it is; a whole photo
                # (direct media has no thumbnails) is made small here.
                got = contacts_photos.thumbnail(data, THUMB_PX, 16 * 1024 * 1024) \
                    if len(data) > 512 * 1024 else None
                fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
                with os.fdopen(fd, "wb") as f:
                    f.write(got[0] if got else data)
            else:
                _download(mxc, path)
        except (urllib.error.URLError, OSError, ValueError) as e:
            log.info("room media %s unread: %s", "thumbnail" if thumb else "file", e)
            fd = os.open(d / f"{name}.none", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            os.close(fd)
            return None
    _prune(d)
    return path


def content_type(info: dict, thumb: bool, path: Path) -> str:
    """What to call it: a thumbnail by its bytes; the whole by its event's
    mimetype when that is a picture, video or sound, else a download."""
    if thumb:
        from . import contacts_photos

        try:
            with open(path, "rb") as f:
                head = f.read(64)
        except OSError:
            head = b""
        return contacts_photos.sniff(head) or "image/jpeg"
    mt = str(info.get("mimetype") or "")
    if mt.split("/")[0] in ("image", "video", "audio") and "svg" not in mt and "/" in mt:
        return mt
    return "application/octet-stream"
