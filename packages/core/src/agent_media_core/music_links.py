"""Spotify albums, playlists and songs shared into the Music tab, played from YouTube.

David, 2 Nov 2026: "Is there a way to share a link to an album from
Spotify?" There is no Spotify player here (Mopidy plays YouTube), so a
Spotify link is read for its track list and each track is found on YouTube
when it is played, as the music bed names his Spotify songs.

Reading needs no Spotify account: the public embed page
(``open.spotify.com/embed/<kind>/<id>``) carries the name, the artist and
the track list (title and artist each) in its page data, and oEmbed gives
the cover. Kept in ``<state_dir>/music-links.json`` as ``{"albums": [{key,
kind, url, title, artist, art, tracks: [{artist, title, vid?}], added}]}``;
a track's YouTube id is kept once found.

`play` finds the first track and starts it on the music channel, then
queues the rest in the background (`python -m … queue <key>`), finding each
as it goes, so the music starts in a second or two. The Music tab lists
them as its `albums` list (music_recent); a row's uri is ``album:<key>``,
which `POST /music {"action": "play"}` hands here.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Optional

from ._paths import cache_dir, state_dir

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


def parse_embed(html: str) -> dict:
    """The embed page's ``{title, artist, tracks: [{artist, title}]}``."""
    m = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', html, re.S)
    if not m:
        raise ValueError("Spotify's page had no track list")
    entity = json.loads(m.group(1))["props"]["pageProps"]["state"]["data"]["entity"]
    tracks = entity.get("trackList") or []
    if not tracks and entity.get("type") == "track":
        tracks = [{"title": entity.get("name") or entity.get("title"),
                   "subtitle": entity.get("subtitle") or ", ".join(
                       a.get("name", "") for a in entity.get("artists") or [])}]
    out = [{"artist": (t.get("subtitle") or "").split(",")[0].strip(), "title": (t.get("title") or "").strip()}
           for t in tracks if t.get("title")]
    return {"title": entity.get("name") or entity.get("title") or "",
            "artist": entity.get("subtitle") or "", "tracks": out}


def read(kind: str, sid: str) -> dict:
    """An album's, playlist's or song's name, artist, cover and tracks, from
    Spotify's public pages. Raises ValueError when it cannot be read."""
    try:
        got = parse_embed(_get(f"https://open.spotify.com/embed/{kind}/{sid}").decode("utf-8", "replace"))
    except (OSError, KeyError, json.JSONDecodeError) as e:
        raise ValueError(f"couldn't read that from Spotify ({e})") from e
    if not got["tracks"]:
        raise ValueError("that Spotify link has no tracks to play")
    try:
        o = json.loads(_get("https://open.spotify.com/oembed?url=" + urllib.parse.quote(
            f"https://open.spotify.com/{kind}/{sid}", safe=""), timeout=8))
        got["art"] = o.get("thumbnail_url")
    except Exception:  # noqa: BLE001 — a cover is a nicety
        got["art"] = None
    return got


def add(url: str, title: str = "") -> dict:
    """Save a Spotify album, playlist or song for the Music tab. Returns
    ``{"added": "music", "id", "title", "tracks"}``; ValueError otherwise."""
    p = parse(url)
    if not p:
        raise ValueError("a music link must be a Spotify album, playlist or song")
    kind, sid = p
    got = read(kind, sid)
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
                    "source": "spotify"})
    return out


# ---- playing -------------------------------------------------------------------

def _vid(track: dict) -> Optional[str]:
    """The YouTube video for a track: the original (not live, a cover or
    karaoke) when the search has it, else the first that is not marked."""
    from . import music_bed_favourites as f

    hits = f.search(f"{track['artist']} {track['title']}")
    best = next((s for s in hits if f.is_original(track, s)), None)
    if best is None:
        best = next((s for s in hits if not f._MARK.search(str(s.get("title") or ""))
                     and not f._NEVER.search(str(s.get("title") or ""))), None)
    return best["id"] if best else None


def _resolve(key: str, i: int) -> Optional[str]:
    """Track `i`'s YouTube id, found once and kept."""
    a = find(key)
    if not a or i >= len(a["tracks"]):
        return None
    t = a["tracks"][i]
    if t.get("vid"):
        return t["vid"]
    try:
        vid = _vid(t)
    except Exception as e:  # noqa: BLE001 — a track not found is skipped
        print(f"music-links: search failed for {t}: {e}", file=sys.stderr)
        vid = None
    if vid:
        albums = _load()
        for x in albums:
            if x["key"] == a["key"] and i < len(x["tracks"]):
                x["tracks"][i]["vid"] = vid
        _save(albums)
    return vid


def _music(*args: str) -> tuple[bool, str]:
    r = subprocess.run([sys.executable, "-m", "agent_media_core.cli", "music", "play", *args],
                       capture_output=True, text=True, timeout=60)
    return r.returncode == 0, (r.stderr or r.stdout or "").strip()[-300:]


def _watch(vid: str) -> str:
    return f"yt:https://www.youtube.com/watch?v={vid}"


def play(key: str) -> dict:
    """Start a saved album on the music channel: its first track found and
    played now, the rest queued in the background."""
    a = find(key)
    if not a:
        return {"ok": False, "error": "no saved album under that id"}
    first = None
    for i in range(min(len(a["tracks"]), 5)):
        vid = _resolve(a["key"], i)
        if vid:
            first = i
            break
    if first is None:
        return {"ok": False, "error": "couldn't find its first tracks on YouTube"}
    ok, said = _music(_watch(a["tracks"][first].get("vid") or _resolve(a["key"], first) or ""))
    if not ok:
        return {"ok": False, "error": said or "music play failed"}
    if first + 1 < len(a["tracks"]):
        log = cache_dir() / "music-links.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        with open(log, "ab") as out:
            subprocess.Popen([sys.executable, "-m", "agent_media_core.music_links", "queue", a["key"], str(first + 1)],
                             stdin=subprocess.DEVNULL, stdout=out, stderr=out, start_new_session=True)
    return {"ok": True, "title": a.get("title"), "tracks": len(a["tracks"])}


def queue(key: str, start: int) -> int:
    """Find and add tracks `start`… after what is playing; how many were added."""
    a = find(key)
    n = 0
    for i in range(start, len(a["tracks"]) if a else 0):
        vid = _resolve(key, i)
        if vid and _music(_watch(vid), "--add")[0]:
            n += 1
    return n


if __name__ == "__main__":
    if len(sys.argv) == 4 and sys.argv[1] == "queue":
        print(f"music-links: queued {queue(sys.argv[2], int(sys.argv[3]))}", file=sys.stderr)
        sys.exit(0)
    print("usage: python -m agent_media_core.music_links queue <key> <from>", file=sys.stderr)
    sys.exit(2)
