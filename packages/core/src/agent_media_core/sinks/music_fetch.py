"""Rooms-side YouTube acquisition: a local file for the house speakers.

YouTube bot-blocks red5's datacenter address, so the rooms path plays a
local file: the audio is fetched on a residential device and copied into
the Mopidy host's cache, and repeats hit the cache. The fetching is not
something a public product may do, so it is a personal-use add-on's
(youtube_addon "youtube-downloads": `rooms_fetch(url)`, 2 Nov 2026: "might
as well do downloads along with it"); this module keeps the cache — where
it is, what is in it — and asks the add-on on a miss.

When there is no add-on, it fails, or MEDIA_MUSIC_ROOMS_FETCH=0,
`ensure_local` returns None and the caller falls back to streaming the
watch URL where streaming is allowed (sinks.music).

Config (all optional):
  MEDIA_MUSIC_ROOMS_SSH
      host that renders rooms audio (runs Mopidy + mopidy-mpv). Default:
      derived from MEDIA_MPD_HOST/MPD_HOST — loopback or this machine means
      local execution, anything else is reached over ssh.
  MEDIA_MUSIC_ROOMS_CACHE
      $HOME-relative cache dir on that host (default ``.cache/music-offline``).
"""

from __future__ import annotations

import logging
import os
import re
import shlex
import socket
import subprocess
from typing import List, Optional, Tuple

from . import music_local

log = logging.getLogger(__name__)

_WATCH_ID_RE = re.compile(
    r"(?:[?&]v=|youtu\.be/|/shorts/|/live/)([A-Za-z0-9_-]{11})")

# Reuse the phone's ControlMaster options so repeated fetches are cheap.
_SSH_OPTS = music_local._SSH_OPTS


def enabled() -> bool:
    return os.environ.get("MEDIA_MUSIC_ROOMS_FETCH", "1") != "0"


def rooms_ssh_host() -> Optional[str]:
    """Host to run rooms-side file commands on, or None for local execution."""
    override = os.environ.get("MEDIA_MUSIC_ROOMS_SSH")
    if override is not None:
        return override.strip() or None
    host = (os.environ.get("MEDIA_MPD_HOST")
            or os.environ.get("MPD_HOST", "127.0.0.1"))
    if host in ("127.0.0.1", "::1", "localhost"):
        return None
    if host.split(".")[0] == socket.gethostname().split(".")[0]:
        return None
    return host


def cache_dir() -> str:
    return os.environ.get("MEDIA_MUSIC_ROOMS_CACHE", ".cache/music-offline")


def watch_id(url: str) -> Optional[str]:
    """The 11-char video id of a YouTube URL/id, or None."""
    u = url.strip()
    m = _WATCH_ID_RE.search(u)
    if m:
        return m.group(1)
    return u if re.fullmatch(r"[A-Za-z0-9_-]{11}", u) else None


def _rooms_run(script: str, *, stdin=None, timeout: float = 30.0
               ) -> subprocess.CompletedProcess:
    """Run a shell snippet on the rooms host (locally when we are it)."""
    host = rooms_ssh_host()
    if host is None:
        argv = ["sh", "-c", script]
    else:
        argv = ["ssh", *_SSH_OPTS, host, script]
    return subprocess.run(argv, stdin=stdin, capture_output=True, text=True,
                          timeout=timeout)


def _cached_path(vid: str) -> Optional[str]:
    r = _rooms_run(
        f"ls -1 \"$HOME\"/{shlex.quote(cache_dir())}/{vid}.* 2>/dev/null"
        " | grep -v -e '\\.title$' -e '\\.part$' | head -1")
    path = (r.stdout or "").strip().splitlines()
    return path[0] if r.returncode == 0 and path else None


def cached_path_for_id(vid: str) -> Optional[str]:
    """Public cache probe for phone-local seeding; never fetches."""
    return _cached_path(vid)


def ensure_local(url: str) -> Optional[str]:
    """Rooms-local file path for a YouTube watch URL, fetching if needed.

    Returns None when fetching is disabled or fails — callers fall back to
    the plain streaming URI.
    """
    if not enabled():
        return None
    vid = watch_id(url)
    if vid:
        cached = _cached_path(vid)
        if cached:
            return cached
    from .. import youtube_addon
    addon = youtube_addon.get("youtube-downloads")
    if addon is None:
        return None
    try:
        return addon.rooms_fetch(url)
    except Exception as e:  # noqa: BLE001 — never break play() on fetch bugs
        log.warning("music_fetch: %s", e)
        return None


def append_fetched(urls: List[str]) -> None:
    """Fetch each URL and append it to the rooms queue (detached helper).

    Used for playlist tails: the first track plays immediately, the rest
    arrive as their downloads finish. Tracks that fail to fetch are appended
    as plain watch URLs so the queue order is preserved.
    """
    from .music import SinkMusic, StreamRefused, _may_stream
    m = SinkMusic()
    for url in urls:
        path = ensure_local(url)
        try:
            uri = f"mpv:{path}" if path else _may_stream(f"mpv:{url}")
        except StreamRefused:
            log.warning("music_fetch: %s couldn't be downloaded and may not stream; skipped", url)
            continue
        try:
            m.enqueue(uri)
        except OSError as e:
            log.warning("music_fetch: enqueue failed: %s", e)
            return


def spawn_append_fetched(urls: List[str]) -> None:
    """Run append_fetched in a detached process so play() returns promptly."""
    if not urls:
        return
    import sys
    subprocess.Popen(
        [sys.executable, "-m", "agent_media_core.sinks.music_fetch", *urls],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        start_new_session=True)


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO)
    append_fetched(sys.argv[1:])
