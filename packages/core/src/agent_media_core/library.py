"""Audiobook library: resolve YouTube URIs to locally-cached files.

A datacenter IP is what makes this necessary: YouTube blocks them
(SABR/PO-token), so a hub hosted in one can neither stream nor download
YouTube directly. (This began on an IONOS host and is equally true of the
current Hetzner one.) The book channel's
mpv therefore cannot play `yt:`/youtube URLs at all. Instead we acquire the
audio on the phone (residential IP) via `audiobook-fetch`, sync it into a
local library, and the book channel plays the *local file*.

This module is the glue: detect a YouTube URI, map it to its cached file (by
the video id yt-dlp embeds in the filename, ``... [<id>].<ext>``), and — on a
miss — kick off `audiobook-fetch` to acquire it.

See memory: project_youtube_acquisition_phone.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Optional, Sequence


# Bare 11-char YouTube id from the common URL shapes (after any `yt:` strip).
_YT_ID = re.compile(
    r"(?:youtube\.com/(?:watch\?(?:.*&)?v=|embed/|shorts/|live/)|youtu\.be/)"
    r"([0-9A-Za-z_-]{11})"
)
_YT_HOST = re.compile(r"^https?://(?:[\w-]+\.)?(?:youtube\.com|youtu\.be)/", re.I)
_YT_LIST = re.compile(r"[?&]list=([0-9A-Za-z_-]+)")


def _suffix(target=None) -> str:
    """Env-var suffix for a target (accepts a Target, a name str, or None)."""
    name = getattr(target, "name", target) or ""
    return str(name).upper().replace("-", "_")


def _tenv(base: str, target=None) -> Optional[str]:
    """Per-target override of an env var, falling back to the global.

    e.g. MEDIA_AUDIOBOOK_ABS_DIR_ALICE -> MEDIA_AUDIOBOOK_ABS_DIR. Mirrors the
    existing MEDIA_SPEECH_PLAYOUT_MS_<TARGET> convention in submit.py.
    """
    if target is not None:
        sfx = _suffix(target)
        if sfx:
            v = os.environ.get(f"{base}_{sfx}")
            if v:
                return v
    return os.environ.get(base)


def library_dir(target=None) -> Path:
    """Where synced audiobook files live (override: MEDIA_AUDIOBOOK_LIB).

    Per-target override: MEDIA_AUDIOBOOK_LIB_<TARGET>.
    """
    override = _tenv("MEDIA_AUDIOBOOK_LIB", target)
    if override:
        return Path(override).expanduser()
    return Path.home() / "media" / "audiobooks"


def abs_import_dir(target=None) -> Path:
    """The audiobook folder: what book_library reads and imports land in.

    Named for the Audiobookshelf that used to scan it (left 11 Nov 2026); the
    name and its variables stay so a host's settings keep meaning the same
    folder. Override with MEDIA_AUDIOBOOK_ABS_DIR / ABS_AUDIOBOOK_DIR (or their
    per-target _<TARGET> forms). ~/audiobooks when it exists; else the
    historical agent-media library.
    """
    override = _tenv("MEDIA_AUDIOBOOK_ABS_DIR", target) or _tenv("ABS_AUDIOBOOK_DIR", target)
    if override:
        return Path(override).expanduser()
    p = Path.home() / "audiobooks"
    return p if p.exists() else library_dir(target)


def is_youtube(uri: str) -> bool:
    u = uri.strip()
    for prefix in ("yt:", "youtube:"):
        if u.startswith(prefix):
            u = u[len(prefix):]
            break
    return bool(_YT_HOST.match(u))


def youtube_playlist_url(uri: str) -> Optional[str]:
    """Canonical YouTube playlist URL, or None for a single video/non-YouTube.

    A watch URL with both ``v=`` and ``list=`` is treated as the single shared
    video, matching the music sink's behavior.
    """
    u = uri.strip()
    for prefix in ("yt:", "youtube:"):
        if u.startswith(prefix):
            u = u[len(prefix):]
            break
    if u.startswith("playlist:"):
        pid = u[len("playlist:"):]
        return f"https://www.youtube.com/playlist?list={pid}" if pid else None
    if not _YT_HOST.match(u) or "/playlist" not in u.lower():
        return None
    m = _YT_LIST.search(u)
    return f"https://www.youtube.com/playlist?list={m.group(1)}" if m else None


def video_id(uri: str) -> Optional[str]:
    m = _YT_ID.search(uri)
    return m.group(1) if m else None


def expand_youtube_playlist(uri: str, *, limit: Optional[int] = None) -> Optional[list[str]]:
    """Expand a YouTube playlist into watch URLs using the active book profile.

    ``yt-profile book use ...`` controls ``active-book.txt``; yt-dlp reads it
    through the user's normal config/cookies, so private account playlists work
    without a separate account selector here.
    """
    purl = youtube_playlist_url(uri)
    if not purl:
        return None
    cap = int(limit or os.environ.get("MEDIA_AUDIOBOOK_PLAYLIST_MAX", "200"))
    from . import youtube_addon
    addon = youtube_addon.get("youtube-metadata")
    if addon is None:
        return None
    try:
        ids = list(addon.playlist_ids(purl, cap, 300))
    except Exception:  # noqa: BLE001 — the single link, as before
        return None
    return [f"https://www.youtube.com/watch?v={vid}" for vid in ids] or None


def cached_path(vid: str, target=None) -> Optional[Path]:
    """The library file for video id `vid` (yt-dlp names it ``... [<vid>].ext``).

    Searches the per-target library dir when `target` is given.
    """
    d = library_dir(target)
    if not d.is_dir():
        return None
    # The bracketed id is unambiguous; match it literally to avoid title clashes.
    hits = sorted(d.glob(f"*[[]{vid}[]]*"))
    return hits[0] if hits else None


def start_fetch_many(urls: Sequence[str], *, play: bool = False, target=None) -> bool:
    """Start downloading one or more YouTube URLs into the library, in the
    background: the personal-use add-on's (youtube_addon "youtube-downloads"
    `start_fetch_many`). False without it, or when it cannot start.

    With `play=True` the last one plays on the book channel when it has
    arrived. With `target`, it lands in that target's audiobook folder and
    the add-on's `media book play` / `media library-scan` calls name it.
    """
    from . import youtube_addon
    addon = youtube_addon.get("youtube-downloads")
    if addon is None:
        return False
    name = getattr(target, "name", target) or ""
    lib = abs_import_dir(target) if target is not None and name else None
    try:
        return bool(addon.start_fetch_many(list(urls), play=play, target=target, lib=lib))
    except Exception:  # noqa: BLE001
        return False


def start_fetch(url: str, *, play: bool = False, target=None) -> bool:
    return start_fetch_many([url], play=play, target=target)
