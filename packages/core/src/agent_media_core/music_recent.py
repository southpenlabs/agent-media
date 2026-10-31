"""What was played, newest first, and which conversation put it on.

The Media tab's Recently played (David, 28 Sep 2026: "it would be handy to
be able to traverse back through the threads in played order" — a Chris
Cornell album started from another thread, and no way to find which).

`history` holds two kinds of music row. One per thing someone asked for,
written by `StateStore.note_play` (a `yt:` or web URI, the title when known,
and since 28 Sep 2026 `extras.source_session`); and one per file a player
loaded, written by `set_now_playing` (a phone path or a loopback URL, with
`extras.interruption`). Only the first kind is a thing to go back to.

A row from before `source_session` was recorded has its conversation
inferred: the newest speech row with a session in the few minutes before
the play — an agent says what it is putting on, or is asked to, just before
it does.

Each kind has other lists beside what was played (`listed`; David, 31 Oct
2026: "let's get some relevant show filters going for each tab"): music
the songs liked and the most played; books the ones in progress, finished,
or all of them (Audiobookshelf's, with this listener's progress) and the
most played; podcasts the newest episodes of Audiobookshelf's podcast
libraries and the most played.
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

from . import media_meta
from ._paths import state_dir
from .state import StateStore

#: How far back from a play its conversation's last speech may be.
_INFER_S = 300.0


def _asked(row: dict) -> bool:
    uri = row.get("uri") or ""
    extras = row.get("extras") if isinstance(row.get("extras"), dict) else {}
    if "interruption" in extras:
        return False
    return not uri.startswith(("/", "http://localhost", "http://127.0.0.1", "file:"))


def _vid(uri: str) -> Optional[str]:
    from .sinks import music_fetch

    return music_fetch.watch_id(uri[3:] if uri.startswith("yt:") else uri)


def art(uri_or_id: str) -> Optional[str]:
    """A picture for a YouTube track: its thumbnail on YouTube's image host
    (public, and reachable from anywhere the video itself is not)."""
    if not uri_or_id:
        return None
    vid = _vid(uri_or_id)
    if not vid and len(uri_or_id) == 11 and all(c.isalnum() or c in "-_" for c in uri_or_id):
        vid = uri_or_id
    return f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg" if vid else None


def _key(uri: str) -> str:
    """One entry per video, whether it was asked for as `yt:` or a link."""
    return _vid(uri) or uri


_TITLES = "music-titles.json"
#: oEmbed lookups per call: a long list without names fills in over a few
#: visits rather than holding up one.
_LOOKUPS = 6


def _titles() -> dict:
    try:
        d = json.loads((state_dir() / _TITLES).read_text())
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _keep_titles(d: dict) -> None:
    try:
        path = state_dir() / _TITLES
        tmp = path.with_suffix(f".{os.getpid()}.tmp")
        tmp.write_text(json.dumps(d))
        tmp.replace(path)
    except OSError:
        pass


def _oembed(vid: str) -> str:
    """YouTube's public name for a video (oEmbed answers a datacenter IP,
    where the video itself does not)."""
    url = "https://www.youtube.com/oembed?format=json&url=" + urllib.parse.quote(
        f"https://www.youtube.com/watch?v={vid}", safe="")
    try:
        with urllib.request.urlopen(url, timeout=4) as r:
            d = json.loads(r.read().decode())
    except Exception:  # noqa: BLE001 — a name is a nicety
        return ""
    title, who = str(d.get("title") or "").strip(), str(d.get("author_name") or "").strip()
    return f"{title} — {who}" if title and who and who.lower() not in title.lower() else title


def _cached_title(vid: str) -> str:
    """The name the rooms cache learned for a YouTube id, if it has one."""
    from .sinks import music_fetch

    try:
        return (Path(os.path.expanduser("~")) / music_fetch.cache_dir() / f"{vid}.title").read_text().strip()
    except OSError:
        return ""


def _speakers(store: StateStore, since: float) -> list[tuple[float, str]]:
    """`(at, session)` of speech rows since `since`, oldest first."""
    out = []
    for r in store.recent_history(sink="speech", limit=2000):
        if (r.get("started_at") or 0) < since:
            continue
        extras = r.get("extras") if isinstance(r.get("extras"), dict) else {}
        sid = extras.get("source_session")
        if sid:
            out.append((float(r["started_at"]), str(sid)))
    out.sort()
    return out


def _inferred(speakers: list[tuple[float, str]], at: float) -> Optional[str]:
    best = None
    for t, sid in speakers:
        if t > at:
            break
        if at - t <= _INFER_S:
            best = sid
    return best


KINDS = ("music", "book", "podcast")
#: What each kind's list can be (`GET /music/recent?list=`), what was played first.
LISTS = {"music": ("recent", "liked", "most"),
         "book": ("recent", "progress", "finished", "all", "most"),
         "podcast": ("recent", "new", "most")}


def recent(limit: int = 40, store: Optional[StateStore] = None,
           kind: str = "music") -> list[dict]:
    """``[{"id", "uri", "title", "art", "at", "session", "inferred", "kind",
    …}]``, newest first, one row per URI (its latest play). Music rows carry
    `artist`, `album`, `song`, `genre`; books and podcasts `author`, `series`,
    `narrator`, `genre` (media_meta: null until looked up)."""
    store = store or StateStore()
    # How far back plays are counted (`plays`): further for a long list.
    window = min(4000, max(400, limit * 10))
    if kind in ("book", "podcast"):
        return _spoken_books(store, kind, limit, window)
    rows = [r for r in store.recent_history(sink="music", limit=window, requested=True)
            if _asked(r)]
    plays: dict[str, int] = {}
    for r in rows:
        plays[_key(r["uri"])] = plays.get(_key(r["uri"]), 0) + 1
    # A name any row learned for a video names all its rows.
    named: dict[str, str] = {}
    for r in rows:
        if (r.get("text") or "").strip():
            named.setdefault(_key(r["uri"]), r["text"].strip())
    seen: set[str] = set()
    picked = []
    for r in rows:
        k = _key(r["uri"])
        if k in seen:
            continue
        seen.add(k)
        picked.append(r)
        if len(picked) >= limit:
            break
    cache = _titles()
    looked = 0
    for r in picked:
        k = _key(r["uri"])
        vid = _vid(r["uri"])
        if named.get(k) or not vid:
            continue
        if k in cache:
            named[k] = cache[k]
            continue
        title = _cached_title(vid)
        if not title and looked < _LOOKUPS:
            looked += 1
            title = _oembed(vid)
        if title:
            named[k] = cache[k] = title
    if looked:
        _keep_titles(cache)
    oldest = min((r["started_at"] for r in picked), default=0.0)
    speakers = _speakers(store, oldest - _INFER_S) if picked else []
    out = []
    for r in picked:
        extras = r.get("extras") if isinstance(r.get("extras"), dict) else {}
        sid = extras.get("source_session")
        inferred = False
        if not sid:
            sid = _inferred(speakers, float(r["started_at"]))
            inferred = sid is not None
        out.append({"id": r["id"], "uri": r["uri"],
                    "title": named.get(_key(r["uri"])) or None, "art": art(r["uri"]),
                    "at": r["started_at"], "session": sid or None, "inferred": inferred,
                    "plays": plays.get(_key(r["uri"]), 1),
                    "kind": "music", **_music_meta(r["uri"], named.get(_key(r["uri"])) or "")})
    wanted = [(f"yt:{_vid(r['uri'])}", "music", {"vid": _vid(r["uri"]), "title": named.get(_key(r["uri"])) or ""})
              for r in picked if _vid(r["uri"])]
    media_meta.fill(wanted)
    return out


def _music_meta(uri: str, title: str) -> dict:
    vid = _vid(uri)
    got = media_meta.known(f"yt:{vid}") if vid else None
    if got:
        return {k: got.get(k) for k in ("artist", "album", "song", "genre")}
    # Not looked up yet: what the title says, with no search behind it.
    artist, song, sure = media_meta.split_title(title.split(" — ")[0], title.split(" — ", 1)[1] if " — " in title else "")
    return {"artist": artist, "album": None, "song": song or None, "genre": None}


# --- books and podcasts ---------------------------------------------------------

#: Where the spoken things a book row can also be live — a conversation read
#: back, the agenda, a note sent to the phone. Those are Threads', not books.
_SPOKEN = ("/conversations/", "/.cache/agent-media/books/", "/agent-media/books/")
_ABS_TTL_S = 600.0
_ABS: list = [0.0, {}]


def _abs_by_file() -> dict:
    """Audiobookshelf's book items by file name, kept ten minutes."""
    if time.time() - _ABS[0] < _ABS_TTL_S:
        return _ABS[1]
    out: dict = {}
    try:
        from . import book_tracks

        ready = book_tracks._abs_ready()
        if ready:
            url, token, libs = ready
            for lib in libs:
                for it in book_tracks._abs_items(url, token, lib["id"]):
                    name = os.path.basename(it.get("relPath") or it.get("path") or "")
                    if name:
                        out[name] = it
                    if it.get("id"):
                        out["id:" + it["id"]] = it
    except Exception:  # noqa: BLE001 — books without their details
        pass
    _ABS[0], _ABS[1] = time.time(), out
    return out


_PODCASTY = re.compile(r"podcast|episode|#\d+|\bep\.?\s*\d+", re.I)
_BOOKY = re.compile(r"audiobook|audiolibro|unabridged|\bbook \d+", re.I)
_YT_ID = re.compile(r"\[([A-Za-z0-9_-]{11})\]")
_UPLOAD = re.compile(r"(?:full )?audiobook\s*[-–|:]\s*(.+?)\s+[-–]\s+(.+)", re.I)
#: Music that went through the book player (a long mix, a playlist).
_MUSICY = re.compile(r"\bmix\b|\bmusic\b|playlist", re.I)
#: The server's own link to an Audiobookshelf item (it can carry a token).
_ABS_LINK = re.compile(r"^https?://(?:127\.0\.0\.1|localhost)(?::\d+)?/api/items/([0-9a-f-]{36})/")
#: YouTube's categories: what a downloaded video's item says for its genre.
#: Right enough for a podcast; never a book's.
_YT_CATEGORIES = {"gaming", "entertainment", "people & blogs", "education", "music",
                  "film & animation", "science & technology", "news & politics",
                  "howto & style", "comedy", "sports", "travel & events",
                  "autos & vehicles", "pets & animals", "nonprofits & activism"}


def _book_file(it: dict) -> Optional[str]:
    """Where an Audiobookshelf item's file is on this host, if it is here."""
    rel = it.get("relPath") or ""
    for root in ("~/audiobooks", "~/media/audiobooks"):
        p = os.path.join(os.path.expanduser(root), rel)
        if rel and os.path.exists(p):
            return p
    return None


def kind_of(uri: str, title: str, genres: list) -> str:
    """"book" or "podcast". A feed's episode (a web address) is a podcast;
    so is a file whose title says so and does not say it is a book."""
    if _BOOKY.search(title or ""):
        return "book"
    if uri.startswith(("http://", "https://")) or _PODCASTY.search(title or ""):
        return "podcast"
    g = {x.lower() for x in genres or []}
    return "book" if not g or "audiobook" in g else "podcast"


def _spoken_books(store: StateStore, kind: str, limit: int, window: int = 400) -> list[dict]:
    rows = store.recent_history(sink="book", limit=window)
    items = _abs_by_file()
    seen: set[str] = set()
    plays: dict[str, int] = {}
    picked = []
    for r in rows:
        uri = r.get("uri") or ""
        extras = r.get("extras") if isinstance(r.get("extras"), dict) else {}
        if not uri.startswith(("/", "http://", "https://", "yt:")) or any(p in uri for p in _SPOKEN) \
                or extras.get("player") == "sasonica":
            continue
        link = _ABS_LINK.match(uri)
        if link:
            # Never handed on (a token rides in it): the item's file stands in.
            it = items.get("id:" + link.group(1))
            uri = (_book_file(it) if it else None) or ""
            if not uri:
                continue
        elif uri.startswith(("http://127.", "http://localhost")):
            continue
        else:
            it = items.get(os.path.basename(urllib.parse.urlparse(uri).path if "://" in uri else uri))
        plays[uri] = plays.get(uri, 0) + 1
        if uri in seen:
            continue
        seen.add(uri)
        meta = (it or {}).get("media", {}).get("metadata", {}) if it else {}
        title = meta.get("title") or extras.get("title") or (r.get("text") or "").strip() \
            or os.path.splitext(os.path.basename(uri))[0]
        genres = meta.get("genres") or []
        if not it and _MUSICY.search(title):
            continue
        k = kind_of(uri, title, genres)
        if k != kind:
            continue
        picked.append((dict(r, uri=uri), it, meta, title, genres))
        if len(picked) >= limit:
            break
    oldest = min((p[0]["started_at"] for p in picked), default=0.0)
    speakers = _speakers(store, oldest - _INFER_S) if picked else []
    out, wanted = [], []
    for r, it, meta, title, genres in picked:
        extras = r.get("extras") if isinstance(r.get("extras"), dict) else {}
        sid = extras.get("source_session")
        inferred = False
        if not sid:
            sid = _inferred(speakers, float(r["started_at"]))
            inferred = sid is not None
        author = (meta.get("authorName") or "").strip() or None
        # A YouTube upload: "FULL AUDIOBOOK - <Author> - <Title>", its
        # "author" the channel that posted it.
        up = _UPLOAD.match(title)
        if up:
            author, title = up.group(1).strip(), up.group(2).strip()
        if not author and kind == "podcast" and "://" in r["uri"]:
            author = urllib.parse.urlparse(r["uri"]).netloc or None
        genre = next((g for g in genres if g.lower() != "audiobook"
                      and (kind == "podcast" or g.lower() not in _YT_CATEGORIES)), None)
        key = f"abs:{it['id']}" if it else None
        if kind == "book" and not genre and key:
            got = media_meta.known(key)
            genre = (got or {}).get("genre")
            if not got:
                wanted.append((key, "book", {"title": title, "author": author or ""}))
        vid = _YT_ID.search(r["uri"])
        out.append({"id": r["id"], "uri": r["uri"], "title": title,
                    "art": art(vid.group(1)) if vid else None,
                    "at": r["started_at"], "session": sid or None, "inferred": inferred,
                    "plays": plays.get(r["uri"], 1), "kind": kind, "author": author,
                    "series": media_meta.series_of(title, meta.get("seriesName") or ""),
                    "narrator": (meta.get("narratorName") or "").strip() or None,
                    "genre": genre})
    media_meta.fill(wanted)
    return out


def listed(kind: str = "music", which: str = "recent", store: Optional[StateStore] = None,
           limit: int = 40) -> list[dict]:
    """One of a kind's lists (LISTS), in Recently played's shape. `recent`
    is what was played; `most` the same by how often (`plays`, then the
    newest); `liked` the songs liked; `progress`, `finished` and `all` a
    book library's (`progress` 0–1, `finished`); `new` podcast episodes,
    newest first (`published`, epoch seconds)."""
    if which == "recent":
        return recent(limit=limit, store=store, kind=kind)
    if which == "most":
        rows = recent(limit=1000, store=store, kind=kind)
        rows.sort(key=lambda r: (-(r.get("plays") or 1), -float(r.get("at") or 0)))
        return rows[:limit]
    if which == "liked":
        return _liked(store or StateStore(), limit)
    if which in ("progress", "finished", "all"):
        return _library_books(which)[:limit if which != "all" else 500]
    if which == "new":
        return _new_episodes(limit)
    return []


def _liked(store: StateStore, limit: int) -> list[dict]:
    """The songs liked (`media music like`), newest first, one per track."""
    seen: set[str] = set()
    out = []
    names = _titles()
    for like in store.list_likes(500, channel="music"):
        uri = like.get("uri") or ""
        if not uri.startswith(("yt:", "http://", "https://")):
            continue
        k = _key(uri)
        if k in seen:
            continue
        seen.add(k)
        title = (like.get("title") or "").strip() or names.get(k) or None
        extras = like.get("extras") if isinstance(like.get("extras"), dict) else {}
        out.append({"id": -int(like["id"]), "uri": uri, "title": title, "art": art(uri),
                    "at": like.get("at") or 0, "session": extras.get("source_session") or None,
                    "inferred": False, "kind": "music", **_music_meta(uri, title or "")})
        if len(out) >= limit:
            break
    return out


_PROGRESS: list = [0.0, {}]
_PROGRESS_TTL_S = 60.0


def _abs_get(path: str):
    """Audiobookshelf's answer to a GET, or None without one configured."""
    from . import book_tracks

    ready = book_tracks._abs_ready()
    if not ready:
        return None
    url, token, _libs = ready
    req = urllib.request.Request(url + path, headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read())


def _abs_progress() -> dict:
    """This listener's progress by Audiobookshelf item: `{id: {progress,
    finished, at}}`, books only (no episode), kept a minute."""
    if time.time() - _PROGRESS[0] < _PROGRESS_TTL_S:
        return _PROGRESS[1]
    out: dict = {}
    try:
        me = _abs_get("/api/me") or {}
        for m in me.get("mediaProgress") or []:
            if m.get("episodeId") or not m.get("libraryItemId"):
                continue
            out[m["libraryItemId"]] = {"progress": float(m.get("progress") or 0.0),
                                       "finished": bool(m.get("isFinished")),
                                       "at": float(m.get("lastUpdate") or 0) / 1000.0}
    except Exception:  # noqa: BLE001 — no progress, not a failure
        pass
    _PROGRESS[0], _PROGRESS[1] = time.time(), out
    return out


def _library_books(which: str) -> list[dict]:
    """The book library's items whose file is on this host: those in
    progress or finished (latest listened first), or all (by title)."""
    progress = _abs_progress()
    out = []
    for key, it in _abs_by_file().items():
        if not key.startswith("id:"):
            continue
        path = _book_file(it)
        if not path:
            continue
        p = progress.get(it["id"])
        if which == "progress" and not (p and not p["finished"] and p["progress"] > 0):
            continue
        if which == "finished" and not (p and p["finished"]):
            continue
        meta = (it.get("media") or {}).get("metadata") or {}
        title = meta.get("title") or os.path.splitext(os.path.basename(path))[0]
        genre = next((g for g in meta.get("genres") or [] if g.lower() != "audiobook"), None)
        if not genre:
            genre = (media_meta.known(f"abs:{it['id']}") or {}).get("genre")
        out.append({"id": it["id"], "uri": path, "title": title, "art": None,
                    "at": (p or {}).get("at") or float(it.get("addedAt") or 0) / 1000.0,
                    "session": None, "inferred": False, "kind": "book",
                    "author": (meta.get("authorName") or "").strip() or None,
                    "series": media_meta.series_of(title, meta.get("seriesName") or ""),
                    "narrator": (meta.get("narratorName") or "").strip() or None,
                    "genre": genre,
                    "progress": round((p or {}).get("progress", 0.0), 3),
                    "finished": bool((p or {}).get("finished"))})
    if which == "all":
        out.sort(key=lambda r: (r["title"] or "").lower())
    else:
        out.sort(key=lambda r: -r["at"])
    return out


def _episode_file(path: str) -> Optional[str]:
    """An episode's file as this host has it: its own path, else by name in
    the audiobook folder (Audiobookshelf reports its container's view)."""
    if path and os.path.isfile(path):
        return path
    root = os.path.expanduser(os.environ.get("AUDIOBOOK_LIB") or os.environ.get("MEDIA_AUDIOBOOK_ABS_DIR")
                              or "~/audiobooks")
    p = os.path.join(root, os.path.basename(path or ""))
    return p if path and os.path.isfile(p) else None


def _new_episodes(limit: int) -> list[dict]:
    """The newest episodes of Audiobookshelf's podcast libraries whose file
    is on this host, newest first."""
    out = []
    try:
        libs = (_abs_get("/api/libraries") or {}).get("libraries") or []
        for lib in libs:
            if lib.get("mediaType") != "podcast":
                continue
            got = _abs_get(f"/api/libraries/{lib['id']}/recent-episodes?limit={int(limit)}") or {}
            for ep in got.get("episodes") or []:
                path = _episode_file(((ep.get("audioFile") or {}).get("metadata") or {}).get("path") or "")
                if not path:
                    continue
                show = ((ep.get("podcast") or {}).get("metadata") or {})
                published = float(ep.get("publishedAt") or ep.get("addedAt") or 0) / 1000.0
                out.append({"id": ep.get("id") or path, "uri": path, "title": ep.get("title") or None,
                            "art": None, "at": published, "published": published,
                            "session": None, "inferred": False, "kind": "podcast",
                            "author": show.get("title") or show.get("author") or None,
                            "series": None, "narrator": None,
                            "genre": next(iter(show.get("genres") or []), None)})
    except Exception:  # noqa: BLE001 — no episodes, not a failure
        pass
    out.sort(key=lambda r: -r["at"])
    return out[:limit]


def playable_book(uri: str, store: Optional[StateStore] = None) -> bool:
    """Whether `uri` is a book or episode one of the lists holds: the only
    files `POST /music {"action": "book"}` will start."""
    for kind in ("book", "podcast"):
        if any(i.get("uri") == uri for i in recent(limit=200, store=store, kind=kind)):
            return True
    if any(i["uri"] == uri for i in _library_books("all")):
        return True
    return any(i["uri"] == uri for i in _new_episodes(100))
