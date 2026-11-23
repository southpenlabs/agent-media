"""Playlists: named lists of songs, kept here, played in order.

David, 23 Nov 2026, after asking for "a main character energy playlist"
from the Music list and getting a list of names with three play buttons:
"We should implement a playlist feature". A playlist is made by an agent
(`media playlist make`) or from the Music tab, and is the Music tab's
`playlists` list (music_recent), a row's uri ``playlist:<id>``.

Kept in ``<state_dir>/music-playlists.json`` as ``{"playlists": [{id,
title, about, by, made, changed, songs: [{artist, title, uri?}]}]}``. A
song is a name (artist and title), found on YouTube when it is played as
a Discover row is (music_discover.resolve); a song added from what was
played keeps its uri, played as it is.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import threading
import time
from pathlib import Path
from typing import Optional

from ._paths import state_dir

MAX_SONGS = 200
MAX_PLAYLISTS = 200
_LOCK = threading.Lock()
_ID = re.compile(r"^[a-z0-9-]{1,48}$")
_URI_PREFIXES = ("yt:", "http://", "https://")


def _store_path() -> Path:
    return state_dir() / "music-playlists.json"


def _load() -> list[dict]:
    try:
        d = json.loads(_store_path().read_text())
    except (OSError, ValueError):
        return []
    pls = d.get("playlists") if isinstance(d, dict) else None
    return [p for p in pls if isinstance(p, dict) and p.get("id")] if isinstance(pls, list) else []


def _save(pls: list[dict]) -> None:
    p = _store_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(f".tmp.{os.getpid()}")
    tmp.write_text(json.dumps({"playlists": pls}, indent=1, ensure_ascii=False))
    tmp.replace(p)


def _slug(title: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:40].strip("-")
    return s or "playlist"


def parse_song(text: str) -> dict:
    """``"Artist - Title"`` (or an en/em dash) as a song; a yt: or web
    address as a song by its uri; a bare title as a title alone."""
    text = (text or "").strip()
    if not text:
        raise ValueError("a song needs a name")
    if text.startswith(_URI_PREFIXES):
        return {"artist": "", "title": "", "uri": text[:500]}
    m = re.match(r"^(.+?)\s+[-–—]\s+(.+)$", text)
    if m:
        return {"artist": m.group(1).strip()[:200], "title": m.group(2).strip()[:300]}
    return {"artist": "", "title": text[:300]}


def _clean_song(s) -> Optional[dict]:
    if isinstance(s, str):
        try:
            return parse_song(s)
        except ValueError:
            return None
    if not isinstance(s, dict):
        return None
    artist = str(s.get("artist") or "").strip()[:200]
    title = str(s.get("title") or "").strip()[:300]
    uri = s.get("uri")
    uri = uri.strip()[:500] if isinstance(uri, str) and uri.strip().startswith(_URI_PREFIXES) else ""
    if not title and not uri:
        return None
    out = {"artist": artist, "title": title}
    if uri:
        out["uri"] = uri
    return out


def _key(pid: str) -> str:
    return pid[9:] if pid.startswith("playlist:") else pid


def find(pid: str) -> Optional[dict]:
    """A playlist by its id (``playlist:<id>`` too) or, failing that, by its
    title, case and spacing aside."""
    pid = _key((pid or "").strip())
    pls = _load()
    hit = next((p for p in pls if p["id"] == pid), None)
    if hit:
        return hit
    want = " ".join(pid.lower().split())
    return next((p for p in pls if " ".join(str(p.get("title") or "").lower().split()) == want), None)


def make(title: str, songs: list, about: str = "", by: str = "") -> dict:
    """A new playlist; a title already used gets a new id all the same."""
    title = " ".join((title or "").split())[:120]
    if not title:
        raise ValueError("a playlist needs a title")
    clean = [c for c in (_clean_song(s) for s in songs or []) if c][:MAX_SONGS]
    with _LOCK:
        pls = _load()
        if len(pls) >= MAX_PLAYLISTS:
            raise ValueError(f"there are already {MAX_PLAYLISTS} playlists: remove one first")
        taken = {p["id"] for p in pls}
        pid = _slug(title)
        while pid in taken:
            pid = f"{_slug(title)[:33]}-{secrets.token_hex(3)}"
        now = time.time()
        p = {"id": pid, "title": title, "about": " ".join((about or "").split())[:300],
             "by": (by or "").strip()[:60], "made": now, "changed": now, "songs": clean}
        pls.append(p)
        _save(pls)
    return p


def _edit(pid: str, fn) -> dict:
    with _LOCK:
        pls = _load()
        p = find(pid)
        if not p:
            raise LookupError("no playlist by that name")
        p = next(q for q in pls if q["id"] == p["id"])
        fn(p)
        p["changed"] = time.time()
        _save(pls)
        return p


def add(pid: str, songs: list) -> dict:
    clean = [c for c in (_clean_song(s) for s in songs or []) if c]
    if not clean:
        raise ValueError("no song to add")

    def go(p):
        if len(p["songs"]) + len(clean) > MAX_SONGS:
            raise ValueError(f"a playlist holds {MAX_SONGS} songs")
        p["songs"].extend(clean)
    return _edit(pid, go)


def remove_song(pid: str, n: int) -> dict:
    """Song `n` (1-based) taken out."""
    def go(p):
        if not isinstance(n, int) or isinstance(n, bool) or not 1 <= n <= len(p["songs"]):
            raise ValueError("no song at that number")
        del p["songs"][n - 1]
    return _edit(pid, go)


def move_song(pid: str, n: int, to: int) -> dict:
    def go(p):
        k = len(p["songs"])
        for v in (n, to):
            if not isinstance(v, int) or isinstance(v, bool) or not 1 <= v <= k:
                raise ValueError("no song at that number")
        p["songs"].insert(to - 1, p["songs"].pop(n - 1))
    return _edit(pid, go)


def rename(pid: str, title: str) -> dict:
    title = " ".join((title or "").split())[:120]
    if not title:
        raise ValueError("a playlist needs a title")

    def go(p):
        p["title"] = title
    return _edit(pid, go)


def delete(pid: str) -> bool:
    with _LOCK:
        p = find(pid)
        if not p:
            return False
        _save([q for q in _load() if q["id"] != p["id"]])
        return True


def song_name(s: dict) -> str:
    if s.get("artist") and s.get("title"):
        return f"{s['artist']} - {s['title']}"
    return s.get("title") or s.get("uri") or "?"


def all_playlists() -> list[dict]:
    return sorted(_load(), key=lambda p: -float(p.get("changed") or p.get("made") or 0))


def items(q: str = "") -> list[dict]:
    """The playlists, the last changed first, in Recently played's row
    shape, each with its songs (``songs``) for the app to show."""
    q = (q or "").lower().strip()
    out = []
    for p in all_playlists():
        songs = p.get("songs") or []
        if q and q not in str(p.get("title") or "").lower() and not any(
                q in song_name(s).lower() for s in songs):
            continue
        out.append({"id": f"playlist:{p['id']}", "uri": f"playlist:{p['id']}", "title": p.get("title"),
                    "art": None, "at": float(p.get("changed") or p.get("made") or 0), "session": None,
                    "inferred": False, "kind": "music", "artist": None, "album": None, "song": None,
                    "genre": None, "tracks": len(songs), "source": "playlist",
                    "about": p.get("about") or None, "by": p.get("by") or None,
                    "songs": [{"n": i + 1, "artist": s.get("artist") or None, "title": s.get("title") or None,
                               "uri": s.get("uri") or None} for i, s in enumerate(songs)]})
    return out
