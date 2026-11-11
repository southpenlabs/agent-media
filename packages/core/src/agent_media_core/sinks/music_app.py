"""A cached track, served to an app on the phone over http.

The app cannot run yt-dlp or read Termux's files, so a YouTube URI is
fetched the way the rooms lane fetches it (``music_fetch.ensure_local``: on
the phone's residential IP, copied into red5's cache) and handed over as a
URL on red5's clip server, the same one books are served from
(``MEDIA_BOOK_HTTP_ROOT`` / ``MEDIA_BOOK_BASEURL_PHONE``, or
``MEDIA_MUSIC_APP_BASEURL`` to override the base), or as a signed link on
the public server (``GET /music/files/<name>?sig=``). Any other http(s) URL
is handed over as it is. ``music_sasonica`` falls back to this when the
phone's own music-files service cannot take a file.

This module was the `abs` music target, music played by Sasonica ABS's
ExoPlayer (``SinkMusicApp``). That app was retired with Audiobookshelf on
11 Nov 2026 (David: "I'd like to move away from audiobookshelf" — "All the
way"); the serving half is what was left.
"""

from __future__ import annotations

import logging
import os
import re
import urllib.parse
from pathlib import Path
from typing import Optional

from . import music_fetch

log = logging.getLogger(__name__)


def _served_url(path: str) -> Optional[str]:
    """An http URL on red5's clip server for a file in the rooms cache.

    The server's root is the agent-media cache, not the music cache, so the
    file is linked in under ``music/``. None when no server is configured or
    the file is on another host (a remote rooms host has its own disk).
    """
    root = os.environ.get("MEDIA_BOOK_HTTP_ROOT", "")
    base = (os.environ.get("MEDIA_MUSIC_APP_BASEURL")
            or os.environ.get("MEDIA_BOOK_BASEURL_PHONE") or "").rstrip("/")
    src = Path(path)
    if not root or not base or music_fetch.rooms_ssh_host() is not None or not src.is_file():
        return None
    link = Path(root).expanduser() / "music" / src.name
    try:
        link.parent.mkdir(parents=True, exist_ok=True)
        if not (link.is_symlink() and link.resolve() == src.resolve()):
            link.unlink(missing_ok=True)
            link.symlink_to(src)
    except OSError as e:
        log.warning("sink-music-app: cannot link %s under %s: %s", src, root, e)
        return None
    public = os.environ.get("MEDIA_PUBLIC_URL", "").strip().rstrip("/")
    if public and _SERVED_NAME.fullmatch(src.name) and src.suffix.lower() in SERVED_TYPES:
        # Off the tailnet: a signed link on the public server, good for this
        # one file (`GET /music/files/<name>?sig=`, served below); the clip
        # server above stays the fallback (David, 2 Nov 2026: "aren't we
        # trying to avoid using the tailnet?" — "yeah, let's do that too").
        return f"{public}/music/files/{src.name}?sig={_sig(src.name)}"
    return f"{base}/music/{urllib.parse.quote(src.name)}"


#: What the public route serves: a cached track's own name, nothing else.
_SERVED_NAME = re.compile(r"[A-Za-z0-9_-][A-Za-z0-9._-]{0,120}")
SERVED_TYPES = {".mka": "audio/x-matroska", ".webm": "audio/webm", ".m4a": "audio/mp4", ".opus": "audio/ogg",
                ".ogg": "audio/ogg", ".mp3": "audio/mpeg", ".flac": "audio/flac", ".aac": "audio/aac"}


def _sig(name: str) -> str:
    from .. import clip_sign

    # Its own scope, so a speech clip's signature never opens a track.
    return clip_sign.sign("music:" + name)


def served(name: str, sig: str) -> Optional[tuple[Path, str]]:
    """A linked track and its content type for `GET /music/files/<name>?sig=`,
    else None (a bad name, signature, or no file alike)."""
    import hmac

    ctype = SERVED_TYPES.get(Path(name or "").suffix.lower())
    if not ctype or not _SERVED_NAME.fullmatch(name or "") or ".." in name:
        return None
    if not sig or not hmac.compare_digest(_sig(name), sig):
        return None
    root = os.environ.get("MEDIA_BOOK_HTTP_ROOT", "")
    if not root:
        return None
    f = Path(root).expanduser() / "music" / name
    return (f, ctype) if f.is_file() else None


def _title_beside(path: str) -> str:
    try:
        return Path(path).with_suffix(".title").read_text().strip()
    except OSError:
        return ""


def resolve(uri: str) -> tuple[Optional[str], str]:
    """``(url, title)`` the app can play for `uri`; url None = it cannot."""
    raw = uri[3:] if uri.startswith("yt:") else uri
    if music_fetch.watch_id(raw) and ("youtu" in raw or uri.startswith("yt:")):
        path = music_fetch.ensure_local(raw)
        return (_served_url(path), _title_beside(path)) if path else (None, "")
    if raw.startswith(("http://", "https://")):
        return raw, ""
    if raw.startswith("/"):
        return _served_url(raw), _title_beside(raw)
    return None, ""
