"""Spotify albums, playlists and songs shared into the Music tab.

David, 2 Nov 2026: "Is there a way to share a link to an album from
Spotify?" A link is kept in ``<state_dir>/music-links.json`` as
``{"albums": [{key, kind, url, title, artist, art, tracks: [{artist,
title, vid?}], added}]}``; the Music tab lists them as its `albums` list
(music_recent), a row's uri ``album:<key>``.

Played on the listener's own Spotify (spotify_remote; `POST /music
{"action": "spotify"}`), with its name and cover from Spotify's documented
oEmbed. Reading its track list from Spotify's embed page and playing each
track from YouTube is a personal install's only: the `spotify-via-youtube`
add-on does it (personal_use; a separately installed package,
docs/proposals/2026-11-02-personal-use-addons.md), asked for `read`,
`play` and `queue` where it is installed and allowed.
"""

from __future__ import annotations

import json
import os
import re
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Optional

from ._paths import state_dir

_SPOTIFY = re.compile(r"^https?://open\.spotify\.com/(?:intl-[a-z-]+/)?(album|playlist|track)/([A-Za-z0-9]{10,40})")
UA = "Mozilla/5.0 (X11; Linux x86_64) agent-media"


def _store_path() -> Path:
    return state_dir() / "music-links.json"


def _load() -> list[dict]:
    try:
        d = json.loads(_store_path().read_text())
    except (OSError, ValueError):
        return []
    albums = d.get("albums") if isinstance(d, dict) else None
    return [a for a in albums if isinstance(a, dict) and a.get("key")] if isinstance(albums, list) else []


def _save(albums: list[dict]) -> None:
    p = _store_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(f".tmp.{os.getpid()}")
    tmp.write_text(json.dumps({"albums": albums}, indent=1))
    tmp.replace(p)


def parse(url: str) -> Optional[tuple[str, str]]:
    """``(kind, id)`` for a Spotify album, playlist or track link."""
    m = _SPOTIFY.match((url or "").strip())
    return (m.group(1), m.group(2)) if m else None


def is_music_link(url: str) -> bool:
    return parse(url) is not None


def _get(url: str, timeout: float = 15.0) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 — open.spotify.com only
        return r.read(4 * 1024 * 1024)


def _oembed_only(kind: str, sid: str) -> dict:
    """What Spotify's documented oEmbed says (name, cover), no tracks: the
    public way, where its embed page is not read (personal_use)."""
    try:
        o = json.loads(_get("https://open.spotify.com/oembed?url=" + urllib.parse.quote(
            f"https://open.spotify.com/{kind}/{sid}", safe=""), timeout=8))
    except Exception as e:  # noqa: BLE001
        raise ValueError(f"couldn't read that from Spotify ({e})") from e
    return {"title": o.get("title") or "Spotify", "artist": None, "art": o.get("thumbnail_url"), "tracks": []}


def add(url: str, title: str = "") -> dict:
    """Save a Spotify album, playlist or song for the Music tab. Returns
    ``{"added": "music", "id", "title", "tracks"}``; ValueError otherwise.
    Its tracks are read only by the spotify-via-youtube add-on, where it is
    installed and allowed (personal_use); elsewhere it is saved by its
    oEmbed name and cover, to play on Spotify itself."""
    from . import personal_use

    p = parse(url)
    if not p:
        raise ValueError("a music link must be a Spotify album, playlist or song")
    kind, sid = p
    addon = personal_use.provider("spotify-via-youtube") if personal_use.allowed("spotify-via-youtube") else None
    got = addon.read(kind, sid) if addon else _oembed_only(kind, sid)
    key = f"spotify-{kind}-{sid}"
    albums = [a for a in _load() if a["key"] != key]
    albums.append({"key": key, "kind": kind, "url": f"https://open.spotify.com/{kind}/{sid}",
                   "title": title.strip() or got["title"], "artist": got["artist"],
                   "art": got.get("art"), "tracks": got["tracks"], "added": time.time()})
    _save(albums)
    return {"added": "music", "id": f"album:{key}", "title": title.strip() or got["title"],
            "artist": got["artist"], "tracks": len(got["tracks"])}


def remove(key: str) -> bool:
    key = key[6:] if key.startswith("album:") else key
    albums = _load()
    left = [a for a in albums if a["key"] != key]
    if len(left) == len(albums):
        return False
    _save(left)
    return True


def find(key: str) -> Optional[dict]:
    key = key[6:] if key.startswith("album:") else key
    return next((a for a in _load() if a["key"] == key), None)


def items() -> list[dict]:
    """The saved albums, newest first, in Recently played's row shape."""
    out = []
    for a in sorted(_load(), key=lambda a: -float(a.get("added") or 0)):
        out.append({"id": f"album:{a['key']}", "uri": f"album:{a['key']}", "title": a.get("title") or None,
                    "art": a.get("art"), "at": float(a.get("added") or 0), "session": None,
                    "inferred": False, "kind": "music", "artist": a.get("artist") or None,
                    "album": a.get("title") if a.get("kind") == "album" else None,
                    "song": None, "genre": None, "tracks": len(a.get("tracks") or []),
                    "source": "spotify", "link": a.get("url")})
    return out


def spotify_uri(key: str) -> Optional[str]:
    """``spotify:<kind>:<id>`` for a saved one, for playing on Spotify
    itself (spotify_remote)."""
    a = find(key)
    if not a:
        return None
    sid = a["key"].rsplit("-", 1)[-1]
    return f"spotify:{a['kind']}:{sid}"


# ---- playing -------------------------------------------------------------------

def play(key: str) -> dict:
    """Play a saved album: through the spotify-via-youtube add-on where it
    is installed and allowed (its first track found on YouTube and played,
    the rest queued), else on the listener's own Spotify (spotify_remote),
    or say so."""
    from . import personal_use

    a = find(key)
    if not a:
        return {"ok": False, "error": "no saved album under that id"}
    addon = personal_use.provider("spotify-via-youtube") if personal_use.allowed("spotify-via-youtube") else None
    if addon and a.get("tracks"):
        return addon.play(key)
    from . import spotify_remote
    r = spotify_remote.play(spotify_uri(key) or "")
    if isinstance(r, dict) and r.get("ok"):
        return {**r, "title": a.get("title"), "on": "spotify"}
    said = r.get("error") if isinstance(r, dict) else None
    return {"ok": False, "error": said or "play it on Spotify: sign in to Spotify in Settings, or open it in Spotify"}


def queue(key: str, start: int) -> int:
    """Tracks `start`… queued after what is playing, by the add-on; 0 without it."""
    from . import personal_use

    addon = personal_use.provider("spotify-via-youtube") if personal_use.allowed("spotify-via-youtube") else None
    return addon.queue(key, start) if addon else 0
