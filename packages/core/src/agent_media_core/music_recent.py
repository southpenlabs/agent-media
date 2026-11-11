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
or all of them (the book library, book_library, with how far he is
through each) and the most played; podcasts the most played and the links
saved for the tab (podcast_links). New episodes of the shows followed are
the Podcasts browser's (podcast_discover; 11 Nov 2026, Audiobookshelf left:
"I'd like to move away from audiobookshelf").
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
    if _NOT_MUSIC.search(uri):
        return False
    return not uri.startswith(("/", "http://localhost", "http://127.0.0.1", "file:"))


#: A saved podcast episode or a book served from red5's clip server, played
#: through the music player (a session's workaround, then a tap on the Music
#: list's row replaying it as music): the Podcasts and Books tabs hold those
#: (David, 3 Nov 2026: a Talking Drupal episode in the Music list — "this
#: looks like it was incorrectly categorised").
_NOT_MUSIC = re.compile(r"^https?://[^/]+/(?:podcast-links|books|conversations)/")


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


def oembed_data(vid: str) -> dict:
    """YouTube's public oEmbed answer for a video (`title`, `author_name`,
    `author_url` — the channel), {} when it does not answer. oEmbed answers
    a datacenter IP, where the video itself does not."""
    url = "https://www.youtube.com/oembed?format=json&url=" + urllib.parse.quote(
        f"https://www.youtube.com/watch?v={vid}", safe="")
    try:
        with urllib.request.urlopen(url, timeout=4) as r:
            d = json.loads(r.read().decode())
    except Exception:  # noqa: BLE001 — a name is a nicety
        return {}
    return d if isinstance(d, dict) else {}


def _oembed(vid: str) -> str:
    """YouTube's public name for a video, with its channel after it unless
    the title already says it."""
    d = oembed_data(vid)
    if not d:
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
LISTS = {"music": ("recent", "liked", "most", "stations", "albums", "sung"),
         "book": ("recent", "progress", "finished", "all", "most", "added", "unstarted"),
         "podcast": ("recent", "most", "saved")}


def recent(limit: int = 40, store: Optional[StateStore] = None,
           kind: str = "music", q: str = "") -> list[dict]:
    """``[{"id", "uri", "title", "art", "at", "session", "inferred", "kind",
    …}]``, newest first, one row per URI (its latest play). Music rows carry
    `artist`, `album`, `song`, `genre`; books and podcasts `author`, `series`,
    `narrator`, `genre` (media_meta: null until looked up). A YouTube row
    whose chapters are known adds `chapters` (each a song: `index`, `title`,
    `start`, `end`, `artist`, `song`, `album`, `genre`) and `mix`
    (music_chapters), and its uploader as `channel` once looked up
    (music_channel.row).

    `q` (9 Nov 2026) searches the whole history instead of the newest
    `limit`: every word of it, case and accents aside, in the title, artist,
    song, album, genre, channel, or a chapter's artist or song (a book's
    author, series, narrator); `limit` matches at most."""
    store = store or StateStore()
    if q.strip():
        if kind in ("book", "podcast"):
            rows = _spoken_books(store, kind, 100000, 100000)
        else:
            rows = _music_rows(store, 100000, None, lookups=False)
        hits = [r for r in rows if matches(r, q)][:limit]
        if kind == "music":
            fill_rows(hits)
        return hits
    # How far back plays are counted (`plays`): further for a long list.
    window = min(4000, max(400, limit * 10))
    if kind in ("book", "podcast"):
        return _spoken_books(store, kind, limit, window)
    out = _music_rows(store, window, limit)
    fill_rows(out)
    return out


def history(store: Optional[StateStore] = None) -> list[dict]:
    """Every music row of the whole history, newest first, one per track,
    in recent()'s shape — no lookups started (music_channel's page)."""
    return _music_rows(store or StateStore(), 100000, None, lookups=False)


def _words(text: str) -> str:
    from . import music_channel

    return re.sub(r"[\W_]+", " ", music_channel.fold(text)).strip()


def matches(row: dict, q: str) -> bool:
    """Whether every word of `q` is in the row's names (case and accents
    aside; a word also found with the spaces taken out, so "vaniborn"
    finds "Vani Born")."""
    words = _words(q).split()
    if not words:
        return True
    names = [row.get(k) for k in ("title", "artist", "album", "song", "genre",
                                   "author", "series", "narrator")]
    ch = row.get("channel") if isinstance(row.get("channel"), dict) else {}
    names += [ch.get("name"), ch.get("uploader"), ch.get("handle")]
    for c in row.get("chapters") or []:
        names += [c.get("artist"), c.get("song"), c.get("title")]
    hay = _words(" ".join(str(n) for n in names if n))
    tight = hay.replace(" ", "")
    return all(w in hay or w in tight for w in words)


def fill_rows(rows: list[dict]) -> None:
    """Start the background lookups the rows still need: names, chapters'
    songs, channels."""
    from . import music_channel, music_chapters

    vids = [v for v in (_vid(r.get("uri") or "") for r in rows) if v]
    wanted = [(f"yt:{v}", "music", {"vid": v, "title": r.get("title") or ""})
              for r in rows for v in [_vid(r.get("uri") or "")] if v]
    for r in rows:
        vid = _vid(r.get("uri") or "")
        if vid:
            wanted += music_chapters.songs(vid, r.get("artist"))[1]
    media_meta.fill(wanted)
    music_chapters.fill(vids)
    music_channel.fill(vids)


def _music_rows(store: StateStore, window: int, limit: Optional[int],
                lookups: bool = True) -> list[dict]:
    """The music rows of the newest `window` plays, `limit` of them (all
    when None). `lookups`: ask oEmbed for a few names not known yet."""
    from . import music_channel

    # A saved podcast played by the phone's music player is a podcast's play
    # (podcast_links.play_url), listed under Podcasts.
    rows = [r for r in store.recent_history(sink="music", limit=window, requested=True)
            if _asked(r) and _PODCAST_FILE not in (r.get("uri") or "")]
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
        if limit is not None and len(picked) >= limit:
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
        if not title and lookups and looked < _LOOKUPS:
            looked += 1
            title = _oembed(vid)
        if title:
            named[k] = cache[k] = title
    if looked:
        _keep_titles(cache)
    oldest = min((r["started_at"] for r in picked), default=0.0)
    speakers = _speakers(store, oldest - _INFER_S) if picked else []
    channels = music_channel.rows([v for v in (_vid(r["uri"]) for r in picked) if v])
    out = []
    for r in picked:
        extras = r.get("extras") if isinstance(r.get("extras"), dict) else {}
        sid = extras.get("source_session")
        inferred = False
        if not sid:
            sid = _inferred(speakers, float(r["started_at"]))
            inferred = sid is not None
        vid = _vid(r["uri"])
        out.append({"id": r["id"], "uri": r["uri"],
                    "title": named.get(_key(r["uri"])) or None, "art": art(r["uri"]),
                    "at": r["started_at"], "session": sid or None, "inferred": inferred,
                    "plays": plays.get(_key(r["uri"]), 1),
                    "kind": "music", **_music_meta(r["uri"], named.get(_key(r["uri"])) or ""),
                    "channel": channels.get(vid) if vid else None})
    _with_chapters(out, fill=False)
    return out


def _with_chapters(rows: list[dict], fill: bool = True) -> list:
    """Give each YouTube row its chapters as songs (`chapters`) and say
    whether it is a mix (`mix`), music_chapters; start reading the ones not
    known. The lookups the chapters still need, for media_meta.fill."""
    from . import music_chapters

    wanted: list = []
    vids = []
    for row in rows:
        vid = _vid(row.get("uri") or "")
        if not vid:
            continue
        vids.append(vid)
        chs, want = music_chapters.songs(vid, row.get("artist"))
        wanted += want
        if chs:
            row["chapters"] = chs
            row["mix"] = music_chapters.is_mix(chs)
    if fill:
        music_chapters.fill(vids)
    return wanted


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
def _meta(e: Optional[dict]) -> dict:
    """A library entry's names as the lists read them."""
    if not e:
        return {}
    return {"title": e.get("title") or "", "authorName": e.get("author") or "",
            "seriesName": e.get("series") or "", "narratorName": e.get("narrator") or "",
            "genres": [e["genre"]] if e.get("genre") else []}


def _cover(e: Optional[dict]) -> Optional[str]:
    if not e:
        return None
    from . import book_library
    return book_library.cover_url(e)


def _library_by_file() -> dict:
    """The book library (book_library) by file name, each with `path`."""
    try:
        from . import book_library
        return book_library.by_name()
    except Exception:  # noqa: BLE001 — books without their details
        return {}


_PODCASTY = re.compile(r"podcast|episode|#\d+|\bep\.?\s*\d+", re.I)
_BOOKY = re.compile(r"audiobook|audiolibro|unabridged|\bbook \d+", re.I)
_YT_ID = re.compile(r"\[([A-Za-z0-9_-]{11})\]")
_UPLOAD = re.compile(r"(?:full )?audiobook\s*[-–|:]\s*(.+?)\s+[-–]\s+(.+)", re.I)
#: Music that went through the book player (a long mix, a playlist).
_MUSICY = re.compile(r"\bmix\b|\bmusic\b|playlist", re.I)
#: YouTube's categories: what a downloaded video's item says for its genre.
#: Right enough for a podcast; never a book's.
_YT_CATEGORIES = {"gaming", "entertainment", "people & blogs", "education", "music",
                  "film & animation", "science & technology", "news & politics",
                  "howto & style", "comedy", "sports", "travel & events",
                  "autos & vehicles", "pets & animals", "nonprofits & activism"}


def kind_of(uri: str, title: str, genres: list) -> str:
    """"book" or "podcast". A feed's episode (a web address) is a podcast;
    so is a file whose title says so and does not say it is a book."""
    if _BOOKY.search(title or ""):
        return "book"
    if uri.startswith(("http://", "https://")) or _PODCASTY.search(title or ""):
        return "podcast"
    g = {x.lower() for x in genres or []}
    return "book" if not g or "audiobook" in g else "podcast"


_PODCAST_FILE = "/podcasts/files/"


def _spoken_books(store: StateStore, kind: str, limit: int, window: int = 400) -> list[dict]:
    rows = store.recent_history(sink="book", limit=window)
    if kind == "podcast":
        # Saved podcasts the phone's music player played (podcast_links.play_url).
        extra = [r for r in store.recent_history(sink="music", limit=window)
                 if _PODCAST_FILE in (r.get("uri") or "")]
        if extra:
            rows = sorted(rows + extra, key=lambda r: -float(r.get("started_at") or 0))[:window]
    saved_art: dict[str, Optional[str]] = {}
    items = _library_by_file()
    seen: set[str] = set()
    plays: dict[str, int] = {}
    picked = []
    for r in rows:
        uri = r.get("uri") or ""
        extras = r.get("extras") if isinstance(r.get("extras"), dict) else {}
        if _PODCAST_FILE in uri:
            from . import podcast_links
            saved = podcast_links.by_play_url(uri)
            if saved is None or kind != "podcast":
                continue
            uri = saved["uri"]
            saved_art[uri] = saved.get("art")
            plays[uri] = plays.get(uri, 0) + 1
            if uri in seen:
                continue
            seen.add(uri)
            picked.append((dict(r, uri=uri, extras={**extras, "author": saved.get("author")}),
                           None, {"authorName": saved.get("author") or ""}, saved.get("title") or uri, []))
            if len(picked) >= limit:
                break
            continue
        if not uri.startswith(("/", "http://", "https://", "yt:")) or any(p in uri for p in _SPOKEN) \
                or extras.get("player") == "sasonica":
            continue
        if uri.startswith(("http://127.", "http://localhost")):
            continue
        else:
            it = items.get(os.path.basename(urllib.parse.urlparse(uri).path if "://" in uri else uri))
        plays[uri] = plays.get(uri, 0) + 1
        if uri in seen:
            continue
        seen.add(uri)
        meta = _meta(it)
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
        key = f"book:{it['id']}" if it else None
        if kind == "book" and not genre and key:
            got = media_meta.known(key)
            genre = (got or {}).get("genre")
            if not got:
                wanted.append((key, "book", {"title": title, "author": author or ""}))
        vid = _YT_ID.search(r["uri"])
        out.append({"id": r["id"], "uri": r["uri"], "title": title,
                    "art": saved_art.get(r["uri"]) or _cover(it) or (art(vid.group(1)) if vid else None),
                    "at": r["started_at"], "session": sid or None, "inferred": inferred,
                    "plays": plays.get(r["uri"], 1), "kind": kind, "author": author,
                    "series": media_meta.series_of(title, meta.get("seriesName") or ""),
                    "narrator": (meta.get("narratorName") or "").strip() or None,
                    "genre": genre})
    media_meta.fill(wanted)
    return out


#: The most a search answers (`GET /music/recent?q=`).
SEARCH_LIMIT = 200


def listed(kind: str = "music", which: str = "recent", store: Optional[StateStore] = None,
           limit: int = 40, q: str = "") -> list[dict]:
    """One of a kind's lists (LISTS), in Recently played's shape. `recent`
    is what was played; `most` the same by how often (`plays`, then the
    newest); `liked` the songs liked; `progress`, `finished` and `all` a
    book library's (`progress` 0–1, `finished`); `saved` the YouTube links
    saved for the Podcasts tab (podcast_links); `albums` the Spotify albums,
    playlists and songs saved for the Music tab (music_links); `sung` every
    song sung out after a long reply, taken up or not (music_sung).

    `q` searches: `recent` and `most` the whole history (recent()), the
    other lists their own rows (`matches`), up to SEARCH_LIMIT."""
    if q.strip():
        if which in ("recent", "most"):
            rows = recent(limit=SEARCH_LIMIT, store=store, kind=kind, q=q)
            if which == "most":
                rows.sort(key=lambda r: (-(r.get("plays") or 1), -float(r.get("at") or 0)))
            return rows
        return [r for r in listed(kind, which, store, limit=1000) if matches(r, q)][:SEARCH_LIMIT]
    if which == "recent":
        return recent(limit=limit, store=store, kind=kind)
    if which == "most":
        rows = recent(limit=1000, store=store, kind=kind)
        rows.sort(key=lambda r: (-(r.get("plays") or 1), -float(r.get("at") or 0)))
        return rows[:limit]
    if which == "liked":
        return _liked(store or StateStore(), limit)
    if which in ("progress", "finished", "all", "added", "unstarted"):
        # `added` and `unstarted` (11 Nov 2026, David: "search and browse for
        # everything"): the whole library newest added first, and the books
        # not started yet, by title.
        return _library_books(which)[:limit if which == "progress" or which == "finished" else 500]
    if which == "albums":
        from . import music_links

        return music_links.items()[:limit]
    if which == "saved":
        from . import podcast_links

        return podcast_links.items()[:limit]
    if which == "stations":
        return _stations(limit)
    if which == "sung":
        from . import music_sung

        return music_sung.items(limit)
    return []


def _stations(limit: int) -> list[dict]:
    """The radio stations to go back to (radio.stations; David, 1 Nov 2026:
    "keep a list of previous radio stations"), as rows whose `uri` is
    `radio:<sid>`: a tap puts the station back on where it was."""
    from . import radio

    out = []
    for s in radio.stations()[:limit]:
        title = s["title"] or "Radio"
        out.append({"id": -int(s["sid"]) if s["sid"].isdigit() else 0,
                    "uri": f"radio:{s['sid']}", "title": f"Radio: {title}",
                    "art": art(s["seed_id"]) if s.get("seed_id") else None,
                    "at": s["stopped"], "session": None, "inferred": False, "kind": "music",
                    "artist": (f"Was on {s['current']}" if s["current"] else None),
                    "song": f"Radio: {title}", "station": s})
    return out


def _liked(store: StateStore, limit: int) -> list[dict]:
    """The songs liked (`media music like`), newest first: one per track,
    and one per song of a mix liked (mix_songs; David, 12 Nov 2026: songs
    inside a mix "liked ... as songs in their own right"). A song of a mix's
    row is named by its song and artist (music_chapters), its `chapterOf`
    the mix's name, its `start` and `end` where it is in the mix (a tap
    plays the mix from its start), its `song_key` the chapter's key."""
    from . import mix_songs

    seen: set[str] = set()
    out = []
    names = _titles()
    likes = store.list_likes(500, channel="music")
    # A clip's name as its own like gave it: a song of the mix's second line.
    clip_names = {_key(r.get("uri") or ""): (r.get("title") or "").strip()
                  for r in likes if not r.get("chapter") and (r.get("title") or "").strip()}
    for like in likes:
        uri = like.get("uri") or ""
        if not uri.startswith(("yt:", "http://", "https://")):
            continue
        k = _key(uri)
        extras = like.get("extras") if isinstance(like.get("extras"), dict) else {}
        ch = mix_songs.from_like(like) if like.get("chapter") else None
        if ch is not None and ch.get("start") is not None:
            sk = ch.get("key") or mix_songs.key(ch["vid"], ch["start"])
            if sk in seen:
                continue
            seen.add(sk)
            mix = (extras.get("mix_title") or "").strip() or names.get(k) or clip_names.get(k) \
                or ("" if (like.get("title") or "") == mix_songs.label(ch) else (like.get("title") or "")) \
                or _cached_title(ch["vid"]) or ""
            out.append({"id": -int(like["id"]), "uri": uri, "title": mix_songs.label(ch),
                        "art": art(uri), "at": like.get("at") or 0,
                        "session": extras.get("source_session") or None, "inferred": False,
                        "kind": "music", "artist": ch.get("artist"), "song": ch.get("song") or ch.get("title"),
                        "album": ch.get("album"), "genre": ch.get("genre"),
                        "start": float(ch["start"]), "end": ch.get("end"),
                        "chapterOf": mix, "song_key": sk})
        else:
            # A whole clip's like (an older like of a chapter not known
            # here counts as its clip's, as it always did).
            if k in seen:
                continue
            seen.add(k)
            title = (like.get("title") or "").strip() or names.get(k) or None
            out.append({"id": -int(like["id"]), "uri": uri, "title": title, "art": art(uri),
                        "at": like.get("at") or 0, "session": extras.get("source_session") or None,
                        "inferred": False, "kind": "music", **_music_meta(uri, title or "")})
        if len(out) >= limit:
            break
    whole = [r for r in out if "song_key" not in r]
    media_meta.fill(_with_chapters(whole))
    return out


def _library_books(which: str) -> list[dict]:
    """The book library's books (book_library): those in progress or
    finished (latest listened first), not started or all (by title), or
    newest added first."""
    from . import book_library

    positions = book_library.resume_positions()
    out = []
    for e in book_library.items():
        p = book_library.progress(e, positions)
        if which == "progress" and not (p["progress"] > 0 and not p["finished"]):
            continue
        if which == "finished" and not p["finished"]:
            continue
        if which == "unstarted" and (p["finished"] or p["progress"] > 0):
            continue
        meta = _meta(e)
        title = meta["title"] or os.path.splitext(os.path.basename(e["path"]))[0]
        genre = next((g for g in meta["genres"] if g.lower() != "audiobook"), None)
        if not genre:
            genre = (media_meta.known(f"book:{e['id']}") or {}).get("genre")
        out.append({"id": e["id"], "uri": e["path"], "title": title, "art": _cover(e),
                    "at": p["at"] or float(e.get("added") or 0),
                    "session": None, "inferred": False, "kind": "book",
                    "author": meta["authorName"] or None,
                    "series": media_meta.series_of(title, meta["seriesName"]),
                    "narrator": meta["narratorName"] or None,
                    "genre": genre, "progress": round(p["progress"], 3),
                    "finished": bool(p["finished"]), "added": float(e.get("added") or 0) or None,
                    "duration": float(e.get("duration") or 0) or None})
    if which in ("all", "unstarted"):
        out.sort(key=lambda r: (r["title"] or "").lower())
    elif which == "added":
        out.sort(key=lambda r: -(r["added"] or 0))
    else:
        out.sort(key=lambda r: -r["at"])
    return out


def playable_book(uri: str, store: Optional[StateStore] = None) -> bool:
    """Whether `uri` is a book or episode one of the lists holds: the only
    files `POST /music {"action": "book"}` will start."""
    for kind in ("book", "podcast"):
        if any(i.get("uri") == uri for i in recent(limit=200, store=store, kind=kind)):
            return True
    if any(i["uri"] == uri for i in _library_books("all")):
        return True
    from . import podcast_links

    return any(i["uri"] == uri for i in podcast_links.items())
