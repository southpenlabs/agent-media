"""Other recordings of a song: live, acoustic, remix, cover, remaster, …

David, 11 Nov 2026: "Can we also have a button for alternative versions of
a song?" — the song card's Versions (`GET /music/versions`, server-contract
§6.9a "Other versions of a song").

**Where they come from**: the YouTube searches the host already has, never a
new one. Mopidy's YouTube search (music_bed_favourites.search, the
personal-use add-on's "youtube-search": many results a query, quick), asked
for the song and for the song with the version words; without it, the
"youtube-radio" add-on's yt-dlp search the DJ uses (radio_dj.search: the
first result a line), one line a kind. Neither: no versions (`source` null).

**Which count**: a result naming the song (its title, words folded) and not
the video shown; one a video, the first of a title. Each gets a `kind` from
its title's words (KINDS, first match), else `cover` when the artist is
named neither in the title nor as the channel, else `original` (another
upload of the same recording: a lyric video, a Topic channel's). At most
LIMIT, the song's own first, then by kind.

**Kept** a while (CACHE_S, six hours) in `<state_dir>/music-versions.json`
by the song's words, the last CACHE_KEEP songs: a search is seconds.
"""

from __future__ import annotations

import json
import os
import re
import time
import unicodedata
from typing import Optional

from ._paths import state_dir

#: Title words → kind, first match wins (so "Live Acoustic" is live).
KINDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("karaoke", ("karaoke",)),
    ("instrumental", ("instrumental", "backing track")),
    ("live", ("live", "concert", "unplugged", "in session", "live session")),
    ("acoustic", ("acoustic", "stripped", "piano version")),
    ("remix", ("remix", "rmx", "mix)", "edit)", "bootleg")),
    ("cover", ("cover", "covered by", "tribute")),
    ("remaster", ("remaster", "remastered")),
    ("extended", ("extended", "12 inch", "12\"", "full length")),
    ("demo", ("demo",)),
    ("slowed", ("slowed", "reverb", "sped up", "nightcore")),
)
ORDER = ("original",) + tuple(k for k, _ in KINDS)
LIMIT = 20
CACHE_S = 6 * 3600.0
CACHE_KEEP = 200
#: The words asked for beside the song itself (and one line each for the DJ's search).
ASKED = ("live", "acoustic", "remix", "cover", "remastered", "extended")


def fold(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def kind_of(title: str, channel: str, artist: str, song: str = "") -> str:
    """The kind of version a title says it is (the song's own words aside:
    "Live Forever" is not live)."""
    t = f" {fold(title)} "
    raw = (title or "").lower()
    if fold(song):
        t = t.replace(f" {fold(song)} ", " ", 1)
        raw = raw.replace((song or "").lower(), " ", 1)
    for kind, words in KINDS:
        for w in words:
            if (f" {fold(w)} " in t) if w.isalnum() or " " in w else (w in raw):
                return kind
    a = fold(artist)
    if a and a not in fold(title) and a not in fold(channel) and a.replace(" ", "") not in fold(channel).replace(" ", ""):
        return "cover"
    return "original"


def _names_song(title: str, song: str) -> bool:
    words = [w for w in fold(song).split() if len(w) > 1] or fold(song).split()
    have = set(fold(title).split())
    return bool(words) and all(w in have for w in words)


def _path():
    return state_dir() / "music-versions.json"


def _cache() -> dict:
    try:
        d = json.loads(_path().read_text())
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _keep(key: str, got: dict, now: float) -> None:
    d = _cache()
    d[key] = {**got, "at": now}
    if len(d) > CACHE_KEEP:
        d = dict(sorted(d.items(), key=lambda kv: -float(kv[1].get("at") or 0))[:CACHE_KEEP])
    try:
        p = _path()
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_name(f".{p.name}.{os.getpid()}")
        tmp.write_text(json.dumps(d, ensure_ascii=False))
        os.replace(tmp, p)
    except OSError:
        pass


def _found(artist: str, song: str) -> tuple[list[dict], Optional[str]]:
    """`[{id, title, channel, dur}]` from whichever search the host has."""
    from . import music_bed_favourites as fav

    q = " ".join(x for x in (artist, song) if x)
    if fav._may_search():
        rows = []
        for ask in (q, f"{q} {' '.join(ASKED[:3])}", f"{q} {' '.join(ASKED[3:])}"):
            rows += [{"id": s["id"], "title": s["title"], "channel": s.get("artist") or "", "dur": s.get("dur") or None}
                     for s in fav.search(ask)]
        return rows, "youtube-search"
    from . import youtube_addon

    if youtube_addon.get("youtube-radio") is not None:
        from . import radio_dj

        lines = [f"{artist} - {song}" if artist else song] + [f"{artist} - {song} {w}" if artist else f"{song} {w}" for w in ASKED]
        return [{"id": s["id"], "title": s["title"], "channel": s.get("channel") or "", "dur": s.get("dur")}
                for s in radio_dj.search(lines)], "youtube-radio"
    return [], None


def versions(artist: str, song: str, exclude: str = "", now: Optional[float] = None) -> dict:
    """`{"query", "source", "versions": [{uri, id, title, channel,
    duration_s, kind}]}` for the song, without the video `exclude`."""
    now = time.time() if now is None else now
    song = (song or "").strip()
    artist = (artist or "").strip()
    key = f"{fold(artist)}|{fold(song)}"
    kept = _cache().get(key)
    if isinstance(kept, dict) and now - float(kept.get("at") or 0) < CACHE_S:
        rows, source = kept.get("rows") or [], kept.get("source")
    else:
        rows, source = _found(artist, song) if song else ([], None)
        if source is not None:
            _keep(key, {"rows": rows, "source": source}, now)
    seen_ids, seen_titles, out = {exclude}, set(), []
    for r in rows:
        vid, title = r.get("id") or "", (r.get("title") or "").strip()
        if not vid or vid in seen_ids or not title or not _names_song(title, song):
            continue
        name = fold(title)
        if name in seen_titles:
            continue
        seen_ids.add(vid)
        seen_titles.add(name)
        dur = r.get("dur")
        out.append({"uri": f"yt:https://www.youtube.com/watch?v={vid}", "id": vid, "title": title,
                    "channel": r.get("channel") or "", "duration_s": int(dur) if isinstance(dur, (int, float)) and dur > 0 else None,
                    "kind": kind_of(title, r.get("channel") or "", artist, song)})
    out.sort(key=lambda v: ORDER.index(v["kind"]))
    return {"query": " ".join(x for x in (artist, song) if x), "source": source, "versions": out[:LIMIT]}
