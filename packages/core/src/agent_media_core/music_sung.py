"""The songs sung out after a long reply, and the ones David took up.

Sing it out (music_bed_vocal.py) plays the original of a favourite on the
phone at the end of a long reply. The phone plays it itself, so until 8 Nov
2026 no history held it (David: "add it to the music history when you
engage" — "maybe you can have its own recently played"). Now:

- **Every song sung out** is kept here as the app reports it starting
  (`started`), newest first: the Media tab's Music list "Sung out"
  (`GET /music/recent?list=sung`, music_recent.listed).
- **A song David takes up** — Keep playing, Like, or Start radio
  (`engaged`, ENGAGED) — also goes into the music history once (a `yt:`
  row in `history`, sink music), so Recently played lists it like music
  someone asked for. A song that only plays to its chorus and fades, or
  one disliked, stays out of it.

Each carries its thread: the conversation whose reply it followed — the
newest speech row whose `extras.bed_song` (music_bed_song) is the same
song (by its YouTube id). Kept in `<state_dir>/music-sung.json`, the last
_KEEP. Never raises into a caller's answer for a missing file.
"""

from __future__ import annotations

import fcntl
import json
import os
import time
from contextlib import contextmanager
from typing import Optional

from ._paths import state_dir

_FILE = "music-sung.json"
#: How many sung-out songs are kept.
_KEEP = 200
#: A report of the same song this soon after the last is the same singing
#: (the app reopened, a status poll after a missed event).
_SAME_S = 900.0
#: What counts as taking a song up: Keep playing, Like, Start radio.
ENGAGED = ("kept", "like", "radio")
#: How far back the reply under it is looked for (speech rows).
_LOOK_BACK = 400


def _path():
    return state_dir() / _FILE


@contextmanager
def _locked():
    p = _path()
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p.with_name(p.name + ".lock"), "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def load() -> list[dict]:
    """The sung-out songs, newest first."""
    try:
        d = json.loads(_path().read_text())
    except (OSError, ValueError):
        return []
    return [e for e in d if isinstance(e, dict)] if isinstance(d, list) else []


def _save(rows: list[dict]) -> None:
    p = _path()
    tmp = p.with_name(f".{p.name}.{os.getpid()}")
    tmp.write_text(json.dumps(rows[:_KEEP], indent=1))
    os.replace(tmp, p)


def _vid_ok(v) -> str:
    v = str(v or "")
    return v if len(v) == 11 and all(c.isalnum() or c in "-_" for c in v) else ""


def _favourite(key: str) -> dict:
    """What the favourites know of `key`: title, artist, video id."""
    try:
        from . import music_bed_favourites as fav

        song = fav._song(fav.load(), key) or {}
    except Exception:  # noqa: BLE001 — what the app said will do
        song = {}
    return {"title": str(song.get("title") or ""), "artist": str(song.get("artist") or ""),
            "video_id": _vid_ok(song.get("original_id") or song.get("id"))}


def thread_of(video_id: str, store=None) -> Optional[str]:
    """The conversation whose reply had this song under it: the newest
    speech row whose `bed_song` is it."""
    if not video_id:
        return None
    try:
        from .state import StateStore

        store = store or StateStore()
        for r in store.recent_history(sink="speech", limit=_LOOK_BACK):
            ex = r.get("extras") if isinstance(r.get("extras"), dict) else {}
            song = ex.get("bed_song")
            if isinstance(song, dict) and song.get("video_id") == video_id and ex.get("source_session"):
                return str(ex["source_session"])
    except Exception:  # noqa: BLE001 — a row without its thread
        pass
    return None


def _entry(rows: list[dict], key: str, title: str, artist: str, video_id: str,
           store, now: float) -> tuple[dict, bool]:
    """The singing of `key` now: the newest one if it began within _SAME_S,
    else a new one put first. Returns it and whether it is new."""
    for e in rows:
        if e.get("key") == key:
            if now - float(e.get("at") or 0) <= _SAME_S:
                return e, False
            break
    known = _favourite(key)
    vid = _vid_ok(video_id) or known["video_id"]
    e = {"id": int(max([int(r.get("id") or 0) for r in rows] + [0]) + 1), "key": key,
         "title": str(title or known["title"]), "artist": str(artist or known["artist"]),
         "video_id": vid, "at": now, "session": thread_of(vid, store), "engaged": []}
    rows.insert(0, e)
    return e, True


def started(key: str, title: str = "", artist: str = "", video_id: str = "",
            store=None, now: Optional[float] = None) -> dict:
    """A song began singing out on the phone: kept for "Sung out"."""
    if not isinstance(key, str) or not key:
        raise ValueError("key must be a favourite's key")
    now = time.time() if now is None else now
    with _locked():
        rows = load()
        e, _ = _entry(rows, key, title, artist, video_id, store, now)
        _save(rows)
    return dict(e)


def label(e: dict) -> str:
    title, artist = e.get("title") or "", e.get("artist") or ""
    return f"{title} — {artist}" if title and artist else title or artist


def uri(vid: str) -> str:
    return f"yt:https://www.youtube.com/watch?v={vid}"


def engaged(key: str, how: str, title: str = "", artist: str = "", video_id: str = "",
            store=None, now: Optional[float] = None) -> dict:
    """David took the song up (`how` one of ENGAGED). Noted on its singing,
    and the first time, a music history row with its thread, so Recently
    played lists it. Start radio's station records only the songs after
    it (radio._note), and Recently played folds a `yt:` and a link to the
    same video into one, so it is listed once."""
    if how not in ENGAGED:
        raise ValueError(f"how must be one of {', '.join(ENGAGED)}")
    if not isinstance(key, str) or not key:
        raise ValueError("key must be a favourite's key")
    now = time.time() if now is None else now
    with _locked():
        rows = load()
        e, _ = _entry(rows, key, title, artist, video_id, store, now)
        if how not in e["engaged"]:
            e["engaged"].append(how)
        record = not e.get("recorded") and bool(e.get("video_id"))
        if record:
            e["recorded"] = now
        _save(rows)
    if record:
        try:
            from .state import StateStore

            (store or StateStore()).add_history(
                sink="music", uri=uri(e["video_id"]), started_at=now, target="sasonica",
                source="sung", content_type="music", text=label(e) or None,
                extras={"sung": key, **({"source_session": e["session"]} if e.get("session") else {})})
        except Exception:  # noqa: BLE001 — the list still has it
            pass
    return dict(e)


def items(limit: int = 40) -> list[dict]:
    """"Sung out", in Recently played's shape: every song sung out, newest
    first, with `engaged` (what David did with it) and `key`."""
    from .music_recent import _music_meta, art

    out = []
    for e in load()[:limit]:
        vid = e.get("video_id") or ""
        name = label(e) or None
        meta = _music_meta(uri(vid), name or "") if vid else {"album": None, "genre": None}
        out.append({"id": -int(e.get("id") or 0), "uri": uri(vid) if vid else "",
                    "title": name, "art": art(vid) if vid else None,
                    "at": e.get("at") or 0, "session": e.get("session") or None,
                    "inferred": False, "kind": "music",
                    **meta, "artist": e.get("artist") or meta.get("artist"),
                    "song": e.get("title") or meta.get("song"),
                    "key": e.get("key"), "engaged": list(e.get("engaged") or []), "sung": True})
    return out
