"""A song inside a mix, liked, turned and disliked as a song in its own right.

David, 11–12 Nov 2026: songs inside a YouTube mix (a clip with chapters;
music_chapters gives each chapter its artist, song, start and end) "should
be liked and rotated as songs in their own right".

**Which song.** A chapter is named stably by its video and where it starts,
in whole seconds (`key`: ``<video id>@<start>``), with its title beside it.
The likes list keeps the chapter's title in its `chapter` column (as `media
music like` always has) and its start, end, artist and song in the like's
extras; a like with no chapter is the whole clip's.

**What is playing.** `playing` finds the chapter a `media music status`
snapshot is in (its `media_id`, `chapter` title and position), from the
chapters music_chapters knows (Sasonica's player keeps a file's chapters
there as it reads them). A clip with fewer than two known chapters has no
chapter songs: its keys act on the whole clip, as before.

**Disliked chapters** are kept in `<state_dir>/mix-songs-disliked.json`
(`{key: {"vid", "start", "title", "at"}}`) and never picked: not by a
station, nor as backing music, nor sung out.

**Picks.** `liked` lists the liked chapters (newest first) and `turned`
the chapters whose rotation was changed (music_rotation keeps a chapter by
its key). Where a choice plays one of them on its own it plays just that
song: from its start to its end (music_segment for a play, a station's
bounded item in radio, a backing-music favourite cut to it).
"""

from __future__ import annotations

import json
import os
import re
import time
from typing import Optional

from ._paths import state_dir

_VID = re.compile(r"^[A-Za-z0-9_-]{11}$")
_KEY = re.compile(r"^([A-Za-z0-9_-]{11})@(\d+)$")
#: How far a start given may be from a chapter's own and still be it.
_NEAR_S = 2.0


def key(vid: str, start: float) -> str:
    """A chapter's stable name: its video and its start in whole seconds."""
    return f"{vid}@{int(round(float(start or 0)))}"


def parse(k: str) -> Optional[tuple[str, int]]:
    """`(video id, start)` of a chapter's key; None for anything else."""
    m = _KEY.match(k or "")
    return (m.group(1), int(m.group(2))) if m else None


def is_key(k: str) -> bool:
    return parse(k) is not None


def label(ch: dict) -> str:
    """"Artist – Song", else the song, else the chapter's own title."""
    artist, song = (ch.get("artist") or "").strip(), (ch.get("song") or "").strip()
    if artist and song:
        return f"{artist} – {song}"
    return song or (ch.get("title") or "").strip()


def chapters(vid: str, clip_artist: Optional[str] = None) -> list[dict]:
    """A video's chapters as songs (music_chapters.songs), each with its
    `key`; [] for fewer than two, or none known."""
    if not _VID.match(vid or ""):
        return []
    from . import music_chapters

    try:
        rows, _ = music_chapters.songs(vid, clip_artist)
    except Exception:  # noqa: BLE001 — no songs is the whole clip
        return []
    out = []
    for r in rows:
        out.append({**r, "vid": vid, "key": key(vid, r.get("start") or 0)})
    return out


def find(vid: str, start: Optional[float] = None, title: str = "",
         pos_s: Optional[float] = None, rows: Optional[list] = None) -> Optional[dict]:
    """The chapter of `vid` named by its `start` (within two seconds), else
    its `title`, else the one `pos_s` is in; None when it has no chapter
    songs or none fits."""
    rows = chapters(vid) if rows is None else rows
    if not rows:
        return None
    if start is not None:
        try:
            s = float(start)
        except (TypeError, ValueError):
            s = None
        if s is not None:
            near = min(rows, key=lambda r: abs(float(r.get("start") or 0) - s))
            if abs(float(near.get("start") or 0) - s) <= _NEAR_S:
                return near
            return None
    t = (title or "").strip()
    if t:
        same = [r for r in rows if (r.get("title") or "").strip() == t]
        if len(same) == 1 or (same and pos_s is None):
            return same[0]
        if same:
            rows = same
    if pos_s is not None:
        at = None
        for r in rows:
            if float(r.get("start") or 0) <= float(pos_s) + 0.5:
                at = r
        return at
    return None


def playing(now: dict) -> Optional[dict]:
    """The chapter song a `media music status` snapshot is in (`media_id`,
    `chapter`, `pos_ms`), or None (no chapters, or not a YouTube video)."""
    vid = str((now or {}).get("media_id") or "")
    if not _VID.match(vid):
        return None
    pos = now.get("pos_ms")
    pos_s = pos / 1000.0 if isinstance(pos, (int, float)) else None
    return find(vid, title=str(now.get("chapter") or ""), pos_s=pos_s)


def shown(ch: Optional[dict]) -> Optional[dict]:
    """A chapter as an answer carries it: `{key, start, end, title, artist,
    song, label}`."""
    if not ch:
        return None
    return {"key": ch.get("key") or key(ch["vid"], ch.get("start") or 0),
            "start": float(ch.get("start") or 0),
            "end": float(ch["end"]) if ch.get("end") is not None else None,
            "title": ch.get("title") or "", "artist": ch.get("artist"),
            "song": ch.get("song"), "label": label(ch)}


# ---- likes ------------------------------------------------------------------------

def like_extras(ch: dict) -> dict:
    """What a chapter's like keeps beside its title."""
    return {"chapter_key": ch.get("key") or key(ch["vid"], ch.get("start") or 0),
            "chapter_start": float(ch.get("start") or 0),
            "chapter_end": float(ch["end"]) if ch.get("end") is not None else None,
            "artist": ch.get("artist"), "song": ch.get("song")}


def _like_start(like: dict) -> Optional[float]:
    ex = like.get("extras") if isinstance(like.get("extras"), dict) else {}
    try:
        return float(ex["chapter_start"]) if ex.get("chapter_start") is not None else None
    except (TypeError, ValueError):
        return None


def like_is(like: dict, ch: dict) -> bool:
    """Whether a likes row is this chapter's: by its start when it kept one,
    else by the chapter's title."""
    if not like.get("chapter"):
        return False
    s = _like_start(like)
    if s is not None:
        return abs(s - float(ch.get("start") or 0)) <= _NEAR_S
    return (like.get("chapter") or "").strip() == (ch.get("title") or "").strip()


def _likes_of(store, vid: str) -> list[dict]:
    return [r for r in store.list_likes(2000, channel="music")
            if (r.get("media_id") or "") == vid or _vid_of(r.get("uri") or "") == vid]


def is_liked(store, ch: dict) -> bool:
    return any(like_is(r, ch) for r in _likes_of(store, ch["vid"]))


def add_like(store, ch: dict, uri: str = "", backend: str = "", note: str = "",
             pos_ms: Optional[int] = None, dur_ms: Optional[int] = None,
             extras: Optional[dict] = None, mix_title: str = "") -> int:
    """Like this chapter (once: a second like of it is not another row);
    `mix_title` the clip's own name, kept as the row's second line."""
    if is_liked(store, ch):
        return 0
    return store.add_like("music", ch["vid"], uri=uri or f"yt:https://www.youtube.com/watch?v={ch['vid']}",
                          title=label(ch), chapter=ch.get("title") or label(ch),
                          pos_ms=pos_ms, dur_ms=dur_ms, backend=backend, note=note,
                          extras={**(extras or {}), **like_extras(ch),
                                  **({"mix_title": mix_title} if mix_title else {})})


def remove_like(store, ch: dict) -> int:
    """Take back this chapter's likes; how many went."""
    n = 0
    for r in _likes_of(store, ch["vid"]):
        if like_is(r, ch):
            n += store.remove_likes("music", r.get("media_id") or ch["vid"], chapter=r["chapter"])
    return n


def _vid_of(uri: str) -> Optional[str]:
    try:
        from .sinks.music_fetch import watch_id
        return watch_id(uri[3:] if uri.startswith("yt:") else uri) if uri else None
    except Exception:  # noqa: BLE001
        return None


def from_like(like: dict) -> Optional[dict]:
    """The chapter song a likes row names, from the chapters known (else
    from what the like kept); None for a whole clip's like."""
    if not like.get("chapter"):
        return None
    vid = _vid_of(like.get("uri") or "") or (like.get("media_id") or "")
    if not _VID.match(vid):
        return None
    ex = like.get("extras") if isinstance(like.get("extras"), dict) else {}
    ch = find(vid, start=_like_start(like), title=str(like.get("chapter") or ""))
    if ch is None and _like_start(like) is not None:
        ch = {"vid": vid, "key": key(vid, _like_start(like)), "index": None,
              "title": like.get("chapter") or "", "start": _like_start(like),
              "end": ex.get("chapter_end"), "artist": ex.get("artist"), "song": ex.get("song"),
              "album": None, "genre": None}
    if ch is None:
        # An older like by title, its chapters not known here: by title alone.
        pos = like.get("pos_ms")
        ch = {"vid": vid, "key": None, "index": None, "title": like.get("chapter") or "",
              "start": None, "end": None, "artist": None, "song": None, "album": None,
              "genre": None, "pos_s": pos / 1000.0 if isinstance(pos, (int, float)) else None}
    return ch


def liked(store=None, limit: int = 200) -> list[dict]:
    """The liked chapter songs with a known start, newest first, one each,
    the disliked left out."""
    if store is None:
        from .state import StateStore
        store = StateStore()
    gone = disliked_keys()
    out, seen = [], set()
    for r in store.list_likes(2000, channel="music"):
        ch = from_like(r)
        if not ch or ch.get("start") is None:
            continue
        k = ch.get("key") or key(ch["vid"], ch["start"])
        if k in seen or k in gone:
            continue
        seen.add(k)
        out.append({**ch, "key": k, "liked_at": r.get("at")})
        if len(out) >= limit:
            break
    return out


# ---- dislikes ------------------------------------------------------------------------

def _disliked_path():
    return state_dir() / "mix-songs-disliked.json"


def disliked_all() -> dict:
    try:
        d = json.loads(_disliked_path().read_text())
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def disliked_keys() -> set:
    return set(disliked_all())


def _write(d: dict) -> None:
    p = _disliked_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(f".{p.name}.{os.getpid()}")
    tmp.write_text(json.dumps(d, ensure_ascii=False))
    os.replace(tmp, p)


def dislike(ch: dict, now: Optional[float] = None) -> str:
    """👎 on a chapter: never picked again. Its likes go. Its key."""
    k = ch.get("key") or key(ch["vid"], ch.get("start") or 0)
    d = disliked_all()
    d[k] = {"vid": ch["vid"], "start": float(ch.get("start") or 0), "title": label(ch),
            "at": time.time() if now is None else now}
    _write(d)
    return k


def undislike(ch: dict) -> bool:
    k = ch.get("key") or key(ch["vid"], ch.get("start") or 0)
    d = disliked_all()
    if d.pop(k, None) is None:
        return False
    _write(d)
    return True


def is_disliked(ch: dict, gone: Optional[set] = None) -> bool:
    k = ch.get("key") or key(ch["vid"], ch.get("start") or 0)
    return k in (disliked_keys() if gone is None else gone)


# ---- rotation ------------------------------------------------------------------------

def turned(sign: int = 0, data: Optional[dict] = None) -> list[dict]:
    """The chapter songs whose rotation was changed (up for `sign` > 0, down
    for < 0, either for 0), the most turned first: `[{vid, key, start, end,
    title, level}]`, the disliked left out."""
    from . import music_rotation

    data = music_rotation.load() if data is None else data
    gone = disliked_keys()
    out = []
    for k, e in data.items():
        got = parse(k)
        if not got or not isinstance(e, dict) or k in gone:
            continue
        n = music_rotation.level(k, data)
        if not n or n * sign < 0:
            continue
        vid, start = got
        out.append({"vid": vid, "key": k, "start": float(e.get("start", start)),
                    "end": e.get("end"), "title": e.get("title") or "", "level": n,
                    "artist": e.get("artist"), "song": e.get("song")})
    out.sort(key=lambda r: -abs(r["level"]))
    return out
