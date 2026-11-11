"""Music to discover: songs not played here yet, for the Music tab's
Discover (server-contract.md §6.9a "Discover"; David, 11 Nov 2026: "Can we
add a discover section for music too?").

**The lists** (LISTS), each from a source that needs no key, or one this
server already has:

  for_you      ListenBrainz's recommendations for the listener's
               ListenBrainz name (music_bed_favourites.listenbrainz_user):
               GET /1/cf/recommendation/user/{user}/recording, its MBIDs
               named through GET /1/metadata/recording (artist, release,
               cover), a page at a time, the names kept on disk
  created_for  ListenBrainz's playlists made for that name (Weekly
               Exploration, Weekly Jams, …): GET /1/user/{user}/playlists/
               createdfor; one, by `playlist`, is its tracks
               (GET /1/playlist/{mbid}, JSPF)
  fresh        new releases from the artists the name listens to
               (GET /1/user/{user}/fresh_releases), else everyone's
               (GET /1/explore/fresh-releases/)
  top_songs    Apple Music's most played songs in MEDIA_MUSIC_REGION
  top_albums   and albums (Apple's public RSS; its new-releases feed is
               gone, and it has no genre feeds, so a genre is filtered here
               from each item's own genres)
  search       (`q`, not one of `lists`) the catalogue by name: the
               host's YouTube search, Mopidy's YouTube Music where it has it
               (11 Nov 2026, David: "search and browse for everything")
  like_playing YouTube's Mix of what is playing (radio.mix, the station's
               own source: the "youtube-radio" add-on), so like the radio
               without starting it

ListenBrainz asks for a User-Agent with contact and at most one call a
second (spaced here). Every list is kept (TTL), whole, and paged here
(PAGE); `played` marks what the music history already has (artist and song,
folded, or both named in a played title), and those are left out unless
asked for.

**Playing one**: a ListenBrainz or Apple item is a name, not a video, so
`resolve` finds it with the host's YouTube search (Mopidy's, else the DJ's,
as music_versions does) when it is played, queued or liked
(`POST /music {"find": {"artist", "title"}}`), and keeps what it found.
A Mix item is a video already (`uri`).

`_get` is the one place that talks HTTP.
"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Optional

LISTENBRAINZ = "https://api.listenbrainz.org/1"
APPLE = "https://rss.marketingtools.apple.com/api/v2"
CAA = "https://coverartarchive.org/release"
USER_AGENT = "agent-media/1 ( https://github.com/southpenlabs/agent-media )"
LISTS = ("for_you", "created_for", "fresh", "top_songs", "top_albums", "like_playing")
#: The lists that need a ListenBrainz name.
LB_LISTS = ("for_you", "created_for", "fresh")
PAGE = 20
TTL = {"for_you": 6 * 3600.0, "created_for": 6 * 3600.0, "playlist": 6 * 3600.0, "fresh": 6 * 3600.0,
       "top_songs": 3600.0, "top_albums": 3600.0, "like_playing": 3600.0}
PLAYED_TTL_S = 600.0
LB_GAP_S = 1.0
META_BATCH = 25

_lock = threading.Lock()
_cache: dict[str, tuple[float, object]] = {}
_lb_last = [0.0]


def region() -> str:
    return ((os.environ.get("MEDIA_MUSIC_REGION") or "").strip() or "AU").lower()


def lb_user() -> str:
    from . import music_bed_favourites as fav
    return fav.listenbrainz_user()


def _get(url: str, timeout: float = 20.0) -> tuple[int, object]:
    """`(status, parsed JSON or None)`. Raises OSError when unreachable."""
    import urllib.error
    import urllib.request
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode() or ""
            code = r.status
    except urllib.error.HTTPError as e:
        return e.code, None
    try:
        return code, json.loads(raw) if raw else None
    except ValueError:
        return code, None


def _lb(path: str) -> tuple[int, object]:
    """A ListenBrainz call, a second after the last."""
    with _lock:
        wait = _lb_last[0] + LB_GAP_S - time.monotonic()
        _lb_last[0] = time.monotonic() + max(0.0, wait)
    if wait > 0:
        time.sleep(wait)
    return _get(LISTENBRAINZ + path)


def clear_cache() -> None:
    with _lock:
        _cache.clear()


def _cached(key: str, ttl: float, fn, now: float):
    with _lock:
        hit = _cache.get(key)
    if hit and now - hit[0] < ttl:
        return hit[1]
    value = fn()
    with _lock:
        _cache[key] = (now, value)
    return value


def _art(mbid, caa_id, size: int = 250) -> Optional[str]:
    if not mbid:
        return None
    return f"{CAA}/{mbid}/{caa_id}-{size}.jpg" if caa_id else f"{CAA}/{mbid}/front-{size}"


def _year(date) -> Optional[int]:
    try:
        return int(str(date)[:4])
    except (TypeError, ValueError):
        return None


# ---- ListenBrainz ---------------------------------------------------------------------------

def _meta_path() -> Path:
    from ._paths import state_dir
    return state_dir() / "music-discover-names.json"


def _meta_load() -> dict:
    try:
        d = json.loads(_meta_path().read_text())
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _meta_save(d: dict) -> None:
    p = _meta_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    if len(d) > 5000:
        d = dict(list(d.items())[-4000:])
    tmp = p.with_name(f".{p.name}.{os.getpid()}")
    tmp.write_text(json.dumps(d))
    os.replace(tmp, p)


def _names(mbids: list[str]) -> dict:
    """Recording MBID → `{title, artist, album, year, art}`, from what is
    kept, else ListenBrainz's metadata (META_BATCH a call)."""
    kept = _meta_load()
    todo = [m for m in mbids if m not in kept]
    changed = False
    for i in range(0, len(todo), META_BATCH):
        part = todo[i:i + META_BATCH]
        code, got = _lb(f"/metadata/recording/?recording_mbids={','.join(part)}&inc=artist%20release")
        if code != 200 or not isinstance(got, dict):
            break
        for m in part:
            r = got.get(m) if isinstance(got.get(m), dict) else {}
            rec, art, rel = r.get("recording") or {}, r.get("artist") or {}, r.get("release") or {}
            kept[m] = {"title": str(rec.get("name") or ""), "artist": str(art.get("name") or ""),
                       "album": str(rel.get("name") or "") or None,
                       "year": rel.get("year") or _year(rec.get("first_release_date")),
                       "art": _art(rel.get("caa_release_mbid"), rel.get("caa_id"))}
            changed = True
    if changed:
        _meta_save(kept)
    return {m: kept[m] for m in mbids if m in kept}


def _for_you(user: str, now: float) -> list[dict]:
    """The recommendations' MBIDs (names come a page at a time)."""
    def ask():
        code, got = _lb(f"/cf/recommendation/user/{user}/recording?count=100")
        if code == 204:
            return []
        if code != 200 or not isinstance(got, dict):
            raise OSError(f"ListenBrainz answered HTTP {code}")
        return [m.get("recording_mbid") for m in (got.get("payload") or {}).get("mbids") or []
                if isinstance(m, dict) and m.get("recording_mbid")]
    mbids = _cached(f"for_you:{user}", TTL["for_you"], ask, now)
    return [{"id": f"lb:{m}", "mbid": m, "kind": "song", "source": "listenbrainz"} for m in mbids]


def _playlists(user: str, now: float) -> list[dict]:
    def ask():
        code, got = _lb(f"/user/{user}/playlists/createdfor?count=25")
        if code != 200 or not isinstance(got, dict):
            raise OSError(f"ListenBrainz answered HTTP {code}")
        out = []
        for p in got.get("playlists") or []:
            p = p.get("playlist") if isinstance(p, dict) else None
            if not isinstance(p, dict) or not p.get("identifier"):
                continue
            ext = (p.get("extension") or {}).get("https://musicbrainz.org/doc/jspf#playlist") or {}
            meta = (ext.get("additional_metadata") or {}).get("algorithm_metadata") or {}
            out.append({"id": str(p["identifier"]).rstrip("/").rsplit("/", 1)[-1],
                        "title": str(p.get("title") or ""), "date": p.get("date"),
                        "kind": meta.get("source_patch") or None,
                        "expires_at": (ext.get("additional_metadata") or {}).get("expires_at")})
        return out
    return _cached(f"created_for:{user}", TTL["created_for"], ask, now)


def _playlist(mbid: str, now: float) -> list[dict]:
    def ask():
        code, got = _lb(f"/playlist/{mbid}")
        if code != 200 or not isinstance(got, dict):
            raise OSError(f"ListenBrainz answered HTTP {code}")
        out = []
        for t in (got.get("playlist") or {}).get("track") or []:
            if not isinstance(t, dict):
                continue
            ident = t.get("identifier")
            ident = ident[0] if isinstance(ident, list) and ident else ident
            rec = str(ident or "").rstrip("/").rsplit("/", 1)[-1]
            ext = ((t.get("extension") or {}).get("https://musicbrainz.org/doc/jspf#track") or {})
            meta = ext.get("additional_metadata") or {}
            out.append({"id": f"lb:{rec or t.get('title')}", "mbid": rec or None, "kind": "song",
                        "source": "listenbrainz", "title": str(t.get("title") or ""),
                        "artist": str(t.get("creator") or ""), "album": t.get("album") or None,
                        "year": None, "art": _art(meta.get("caa_release_mbid"), meta.get("caa_id"))})
        return out
    return _cached(f"playlist:{mbid}", TTL["playlist"], ask, now)


def _fresh(user: str, now: float) -> list[dict]:
    def ask():
        rows = []
        if user:
            code, got = _lb(f"/user/{user}/fresh_releases?days=30&past=true&future=false")
            if code == 200 and isinstance(got, dict):
                rows = (got.get("payload") or {}).get("releases") or []
        sitewide = not rows
        if sitewide:
            code, got = _lb("/explore/fresh-releases/?days=14&past=true&future=false")
            if code != 200 or not isinstance(got, dict):
                raise OSError(f"ListenBrainz answered HTTP {code}")
            rows = (got.get("payload") or {}).get("releases") or []
        out = []
        for r in rows:
            if not isinstance(r, dict) or not r.get("release_name"):
                continue
            out.append({"id": f"lbr:{r.get('release_mbid') or r['release_name']}", "kind": "album",
                        "source": "listenbrainz", "title": str(r["release_name"]),
                        "artist": str(r.get("artist_credit_name") or ""), "album": str(r["release_name"]),
                        "year": _year(r.get("release_date")), "date": r.get("release_date"),
                        "type": r.get("release_group_primary_type") or None,
                        "genres": [str(g) for g in r.get("release_tags") or [] if g][:5],
                        "art": _art(r.get("caa_release_mbid"), r.get("caa_id")), "sitewide": sitewide})
        out.sort(key=lambda i: str(i.get("date") or ""), reverse=True)
        return out
    return _cached(f"fresh:{user}", TTL["fresh"], ask, now)


# ---- Apple's charts -------------------------------------------------------------------------

def _apple(what: str, now: float) -> list[dict]:
    reg = region()

    def ask():
        code, got = _get(f"{APPLE}/{reg}/music/most-played/100/{what}.json")
        if code != 200 or not isinstance(got, dict):
            raise OSError(f"Apple's charts answered HTTP {code}")
        out = []
        for r in (got.get("feed") or {}).get("results") or []:
            if not isinstance(r, dict) or not r.get("name"):
                continue
            art = str(r.get("artworkUrl100") or "").replace("100x100bb", "300x300bb") or None
            out.append({"id": f"apple:{r.get('id')}", "kind": "song" if what == "songs" else "album",
                        "source": "apple", "title": str(r["name"]), "artist": str(r.get("artistName") or ""),
                        "album": None if what == "songs" else str(r["name"]), "year": _year(r.get("releaseDate")),
                        "genres": [g["name"] for g in r.get("genres") or [] if isinstance(g, dict)
                                   and g.get("name") and g["name"] != "Music"],
                        "art": art, "link": r.get("url") or None})
        return out
    return _cached(f"apple:{reg}:{what}", TTL["top_" + what], ask, now)


# ---- like what is playing -------------------------------------------------------------------

def _like_playing(vid: str, now: float) -> list[dict]:
    from . import radio, radio_io

    def ask():
        out = []
        for s in radio.mix(vid):
            if not s.get("id") or s["id"] == vid:
                continue
            _line, artist, title = radio_io.query(s)
            out.append({"id": f"yt:{s['id']}", "kind": "song", "source": "youtube", "uri": f"yt:{s['id']}",
                        "title": title or str(s.get("title") or ""), "artist": artist or None, "album": None,
                        "year": None, "art": f"https://i.ytimg.com/vi/{s['id']}/mqdefault.jpg"})
        return out
    return _cached(f"mix:{vid}", TTL["like_playing"], ask, now)


# ---- played here already --------------------------------------------------------------------

def _fold(s) -> str:
    from . import music_recent
    return music_recent._words(str(s or ""))


def _played(now: float) -> tuple:
    """What the music history holds: `{"artist|song"}`, the played titles
    (folded) and the uris; kept PLAYED_TTL_S."""
    def ask():
        from . import music_recent
        pairs, titles, uris = set(), [], set()
        for r in music_recent.history():
            a, s = _fold(r.get("artist")), _fold(r.get("song"))
            if s:
                pairs.add(f"{a}|{s}")
            if r.get("title"):
                titles.append(_fold(r["title"]))
            if r.get("uri"):
                uris.add(r["uri"])
        return pairs, titles, uris
    return _cached("played", PLAYED_TTL_S, ask, now)


def was_played(item: dict, played: tuple) -> bool:
    pairs, titles, uris = played
    if item.get("uri") and item["uri"] in uris:
        return True
    a, t = _fold(item.get("artist")), _fold(item.get("title"))
    if not t:
        return False
    if f"{a}|{t}" in pairs:
        return True
    return bool(a) and any(t in x and a in x for x in titles)


# ---- the listing ----------------------------------------------------------------------------

SEARCH_TTL_S = 3600.0


def _search(q: str, now: float) -> list[dict]:
    """The catalogue by name: the host's YouTube search (Mopidy's YouTube
    Music results, else the DJ's first result), kept an hour."""
    from . import music_bed_favourites as fav

    def ask():
        out = []
        if fav._may_search():
            for s_ in fav.search(q)[:40]:
                out.append({"id": f"yt:{s_['id']}", "kind": "song", "source": "youtube", "uri": f"yt:{s_['id']}",
                            "title": str(s_.get("title") or ""), "artist": s_.get("artist") or None,
                            "album": None, "year": None, "duration_s": s_.get("dur") or None,
                            "art": f"https://i.ytimg.com/vi/{s_['id']}/mqdefault.jpg"})
        else:
            from . import radio_dj, radio_io
            for s_ in radio_dj.search([q]):
                _line, artist, title = radio_io.query(s_)
                out.append({"id": f"yt:{s_['id']}", "kind": "song", "source": "youtube", "uri": f"yt:{s_['id']}",
                            "title": title or str(s_.get("title") or ""), "artist": artist or None, "album": None,
                            "year": None, "art": f"https://i.ytimg.com/vi/{s_['id']}/mqdefault.jpg"})
        return out
    return _cached(f"search:{q.lower()}", SEARCH_TTL_S, ask, now)


def listing(which: str, page: int = 1, hide_played: bool = True, genre: str = "", playlist: str = "",
            now_vid: str = "", now: Optional[float] = None, q: str = "") -> dict:
    """What GET /music/discover answers (less `ok`). ValueError for a list
    that is not one or not available here; OSError when its source fails."""
    if which not in LISTS + ("search",):
        raise ValueError(f"list must be one of {', '.join(LISTS)}, or search")
    q = " ".join(str(q or "").split())[:200]
    if which == "search" and len(q) < 2:
        raise ValueError("search needs q, two letters or more")
    now = time.time() if now is None else now
    user = lb_user()
    available = [x for x in LISTS if (x not in LB_LISTS or user or x == "fresh")]
    out: dict = {"list": which, "lists": available, "page": max(1, page), "more": False,
                 "hide_played": hide_played, "items": [], "genres": [], "user": user or None}
    if which in ("for_you", "created_for") and not user:
        raise ValueError("no ListenBrainz name here: set it in Settings → Music → About you")
    if which == "created_for" and not playlist:
        out["playlists"] = _playlists(user, now)
        return out
    if which == "like_playing" and not now_vid:
        out["reason"] = "nothing is playing that has a Mix"
        return out
    if which == "search":
        items = _search(q, now)
        out["q"] = q
    elif which == "for_you":
        items = _for_you(user, now)
    elif which == "created_for":
        if not all(c.isalnum() or c == "-" for c in playlist) or len(playlist) > 64:
            raise ValueError("playlist is one of the lists' ids")
        items = _playlist(playlist, now)
        out["playlist"] = playlist
        pl = next((p for p in _playlists(user, now) if p["id"] == playlist), None)
        out["playlist_title"] = pl["title"] if pl else None
    elif which == "fresh":
        items = _fresh(user, now)
    elif which == "like_playing":
        items = _like_playing(now_vid, now)
    else:
        items = _apple("songs" if which == "top_songs" else "albums", now)
    items = [dict(i) for i in items]
    out["genres"] = sorted({g for i in items for g in i.get("genres") or []}, key=str.lower)
    if genre:
        g = genre.strip().lower()
        items = [i for i in items if g in [x.lower() for x in i.get("genres") or []]]
    try:
        played = _played(now)
    except Exception:  # noqa: BLE001 — nothing marked, not no list
        played = (set(), [], set())
    start = (out["page"] - 1) * PAGE
    shown: list[dict] = []
    i = 0
    # Names come a page at a time (for_you): fill as far as the page needs.
    while i < len(items) and len(shown) < start + PAGE:
        chunk = items[i:i + PAGE]
        i += PAGE
        need = [c["mbid"] for c in chunk if c.get("mbid") and not c.get("title")]
        if need:
            names = _names(need)
            for c in chunk:
                if c.get("mbid") in names and not c.get("title"):
                    c.update(names[c["mbid"]])
        for c in chunk:
            if not c.get("title"):
                continue
            c["played"] = was_played(c, played)
            if hide_played and c["played"]:
                continue
            shown.append(c)
    out["items"] = shown[start:start + PAGE]
    out["more"] = len(shown) > start + PAGE or i < len(items)
    return out


# ---- finding one to play --------------------------------------------------------------------

def _resolved_path() -> Path:
    from ._paths import state_dir
    return state_dir() / "music-discover-found.json"


def resolve(artist: str, title: str) -> Optional[str]:
    """A `yt:` uri for a song or album by name, from the host's YouTube
    search (Mopidy's, else the DJ's); kept. None when there is no search
    or it found nothing."""
    from . import music_bed_favourites as fav
    key = f"{_fold(artist)}|{_fold(title)}"
    try:
        kept = json.loads(_resolved_path().read_text())
        kept = kept if isinstance(kept, dict) else {}
    except (OSError, ValueError):
        kept = {}
    if kept.get(key):
        return kept[key]
    q = " ".join(x for x in (artist, title) if x).strip()
    if not q:
        return None
    vid = None
    if fav._may_search():
        got = fav.search(q)
        vid = got[0]["id"] if got else None
    else:
        from . import radio_dj
        got = radio_dj.search([f"{artist} - {title}" if artist else title])
        vid = got[0]["id"] if got else None
    if not vid:
        return None
    uri = f"yt:{vid}"
    kept[key] = uri
    p = _resolved_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(f".{p.name}.{os.getpid()}")
    tmp.write_text(json.dumps(dict(list(kept.items())[-2000:])))
    os.replace(tmp, p)
    return uri
