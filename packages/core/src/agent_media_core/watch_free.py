"""Free films that play in the app's own player (server-contract.md §6.54
"Free films"; David, 13 Nov 2026, to "a short fixed allow-list of
legitimate sources … so it can't become a back door": "yeah, let's do
that").

**The line.** The sources are the table below, compiled in. Nothing here
takes an address from a setting, a variable or a request: no add-on URL,
no torrent, magnet or info-hash, no debrid, no scraping of a streaming
site. A file's address is built here from the source's own host, an item
id and a file name, and only for an item that passes the source's rule.

  archive   the Internet Archive (archive.org), a library: its Feature
            Films collection, and of that only the items whose own page
            states a public-domain dedication or a Creative Commons
            licence (`licenseurl`). Read through its public search and
            metadata APIs (advancedsearch.php, /metadata/<id>), which it
            offers for this; the film is its own H.264 MP4 derivative,
            streamed by the phone straight from archive.org (ranged
            requests; nothing is proxied through this server).

Looked at and left out (13 Nov 2026): Stremio's "Public Domain Movies"
add-on (Stremio/stremio-public-domain: not touched since 2023, and the
Archive is the library behind such lists); OpenSubtitles and MDBList
(each needs its own key and terms read with care: not added here).

  browse(...)      the films, most downloaded or newest first, by words,
                   genre and years; each matched to Trakt's movie (an IMDb
                   id the item names, else title and year) so its page,
                   ratings and Watched/Watchlist keys work
  file(...)        a film's playable file: by the Archive's item id, or
                   found for a title and year (a title's page asks)
  subtitles(...)   a subtitle file the item ships, as WebVTT (the app
                   cannot read another host's track; this is text, not
                   the film). The Archive's machine transcripts (.asr.srt)
                   are left out: they are not good enough to read.

HTTP goes through `_get` and `_get_text` (the seams the tests stub), only
ever to HOST. Kept an hour.
"""

from __future__ import annotations

import html
import json
import re
import threading
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

from . import trakt as T

HOST = "https://archive.org"
#: The fixed table of sources: id → what it is. One, on purpose.
SOURCES = {"archive": {"name": "Internet Archive", "site": HOST,
                       "terms": "https://archive.org/about/terms.php"}}
#: The rule an Archive item must pass, as its search says it and as
#: `_allowed` checks it again on the item's own metadata.
RULE = "collection:feature_films AND mediatype:movies AND licenseurl:(*publicdomain* OR *creativecommons*)"
_LICENCE = re.compile(r"^https?://(www\.)?creativecommons\.org/(licenses|publicdomain)/", re.I)
ATTRIBUTION = ("Films from the Internet Archive (archive.org), each offered there as public domain "
               "or under a Creative Commons licence.")
USER_AGENT = T.USER_AGENT
LISTS = {"popular": "downloads desc", "recent": "addeddate desc"}
#: Genre slug → (name, the Archive's subject words).
GENRES = {"adventure": ("Adventure", ("adventure",)), "animation": ("Animation", ("animation", "cartoon")),
          "comedy": ("Comedy", ("comedy",)), "crime": ("Crime", ("crime",)), "drama": ("Drama", ("drama",)),
          "film-noir": ("Film Noir", ("film noir", "noir")), "horror": ("Horror", ("horror",)),
          "mystery": ("Mystery", ("mystery",)), "romance": ("Romance", ("romance",)),
          "science-fiction": ("Science Fiction", ("sci-fi", "science fiction")),
          "silent": ("Silent", ("silent",)), "thriller": ("Thriller", ("thriller",)),
          "war": ("War", ("war",)), "western": ("Western", ("western",))}
PAGE_SIZE = 20
PAGE_MAX = 200
TTL_S = 3600.0
MATCH_TTL_S = 86400.0
#: The Archive's video formats the phone plays, best first. Its MP4 derivatives only.
VIDEO_FORMATS = ("h.264", "h.264 ia", "h.264 hd", "mpeg4", "512kb mpeg4")
SUBTITLE_MAX_BYTES = 2_000_000
_IDENT = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}")
_IMDB = re.compile(r"tt\d{5,10}")

_lock = threading.Lock()
_cache: dict[str, tuple[float, object]] = {}


def clear_cache() -> None:
    with _lock:
        _cache.clear()


def _cached(key: str, now: float, fn, ttl: float = TTL_S):
    with _lock:
        hit = _cache.get(key)
    if hit and now - hit[0] < ttl:
        return hit[1]
    value = fn()
    with _lock:
        _cache[key] = (now, value)
    return value


def _open(path: str, params=None, timeout: float = 20.0) -> bytes:
    """GET HOST+path. The only place an address is opened, and only HOST's."""
    import urllib.request
    if not re.match(r"/[A-Za-z0-9]", path) or "://" in path or "@" in path:
        raise ValueError("a path on the source's own host")
    url = HOST + path + ("?" + urllib.parse.urlencode(params, doseq=True) if params else "")
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "accept": "*/*"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read(SUBTITLE_MAX_BYTES * 4)


def _get(path: str, params=None) -> dict:
    got = json.loads(_open(path, params).decode("utf-8", "replace") or "{}")
    return got if isinstance(got, dict) else {}


def _get_text(path: str) -> str:
    raw = _open(path)
    if len(raw) > SUBTITLE_MAX_BYTES:
        raise ValueError("that subtitle file is too large")
    return raw.decode("utf-8-sig", "replace")


def _fold(x) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(x or "").lower()).strip()


def _first(v):
    return v[0] if isinstance(v, list) and v else v


def _year(v) -> Optional[int]:
    m = re.search(r"\d{4}", str(_first(v) or ""))
    return int(m.group(0)) if m and 1870 <= int(m.group(0)) <= 2100 else None


def _minutes(v) -> Optional[int]:
    """The Archive's `runtime` in minutes: "1:31:44", "51:56", "108 min"."""
    s = str(_first(v) or "").strip().lower()
    m = re.fullmatch(r"(?:(\d{1,2}):)?(\d{1,3}):(\d{2})(?:\.\d+)?", s)
    if m:
        total = int(m.group(1) or 0) * 60 + int(m.group(2)) + (1 if int(m.group(3)) >= 30 else 0)
        return total or None
    m = re.match(r"(\d{1,3})\s*(min|m\b)", s)
    return int(m.group(1)) or None if m else None


def _plain(text) -> str:
    """A description as words: the Archive's carry HTML."""
    t = re.sub(r"<[^>]{0,300}>", " ", str(_first(text) or ""))
    return T._short(html.unescape(t))


def _title(raw) -> str:
    """The item's title without a year in brackets at its end."""
    return re.sub(r"\s*[\[(]\d{4}[\])]\s*$", "", " ".join(str(_first(raw) or "").split())).strip()


def _imdb(doc: dict) -> Optional[str]:
    """An IMDb id the item names: its external identifier, or a link in its description."""
    ext = doc.get("external-identifier")
    for e in ext if isinstance(ext, list) else [ext]:
        m = re.fullmatch(r"urn:imdb:(tt\d{5,10})", str(e or "").strip())
        if m:
            return m.group(1)
    m = re.search(r"imdb\.com/title/(tt\d{5,10})", str(doc.get("description") or ""))
    return m.group(1) if m else None


def _match(title: str, year: Optional[int], imdb: Optional[str], now: float) -> Optional[dict]:
    """Trakt's movie for an Archive film, or None: by the IMDb id it names,
    else the first of Trakt's results with the same title and year (a year
    either side counts: a release date and a copyright date differ)."""
    if not T.set_up():
        return None

    def find():
        if imdb:
            rows = T._public_get(f"/search/imdb/{imdb}?type=movie&extended=full")
            for r in rows if isinstance(rows, list) else []:
                o = r.get("movie") if isinstance(r, dict) else None
                if isinstance(o, dict) and o.get("ids"):
                    return o
        if not title or not year:
            return None
        rows = T._public_get(f"/search/movie?query={urllib.parse.quote(title)}&fields=title"
                             f"&years={year - 1}-{year + 1}&extended=full&limit=5")
        for r in rows if isinstance(rows, list) else []:
            o = r.get("movie") if isinstance(r, dict) else None
            if isinstance(o, dict) and o.get("ids") and _fold(o.get("title")) == _fold(title) \
                    and isinstance(o.get("year"), int) and abs(o["year"] - year) <= 1:
                return o
        return None
    try:
        return T._cached(f"d:free:{imdb or ''}:{_fold(title)}:{year}", now, find, MATCH_TTL_S)
    except Exception:  # noqa: BLE001 — unmatched this time: the film still plays
        return None


def _item(doc: dict, now: float) -> Optional[dict]:
    """A §6.54 movie for one of the Archive's search rows, with `free`."""
    ident = str(doc.get("identifier") or "")
    title = _title(doc.get("title"))
    if not _IDENT.fullmatch(ident) or not title:
        return None
    year = _year(doc.get("year"))
    free = {"source": "archive", "id": ident, "page": f"{HOST}/details/{ident}"}
    o = _match(title, year, _imdb(doc), now)
    if o:
        item = T._movie(o)
    else:
        subjects = doc.get("subject")
        subjects = subjects if isinstance(subjects, list) else [s for s in str(subjects or "").split(";")]
        known = {w: name for name, words in GENRES.values() for w in words}
        genres = list(dict.fromkeys(known[_fold(s)] for s in subjects if _fold(s) in known))
        item = {"id": f"movie:free:{ident}", "kind": "movie", "title": title, "year": year,
                "runtime": _minutes(doc.get("runtime")), "genres": genres,
                "overview": _plain(doc.get("description")), "url": free["page"], "ids": {}}
    item["poster"] = f"{HOST}/services/img/{ident}"
    item["free"] = free
    return item


def _words(q: str) -> list[str]:
    """A box's words as plain words: nothing of the Archive's query syntax, its operators included."""
    return [w for w in re.findall(r"[^\W_]+", str(q or ""), re.UNICODE) if w not in ("AND", "OR", "NOT", "TO")][:8]


def browse(which: str = "", q: str = "", genre: str = "", year: str = "", page: int = 1,
           hide_watched: bool = False, now: Optional[float] = None) -> dict:
    """What GET /watch/free answers (less `ok`): Discover's shape. ValueError
    for a bad list, genre, year or page; other errors pass through."""
    which = which or "popular"
    if which not in LISTS:
        raise ValueError(f"list must be one of {', '.join(LISTS)}")
    genre = str(genre or "").strip().lower()
    if genre and genre not in GENRES:
        raise ValueError("genre is one of the genres' slugs")
    span = T._years(year)
    page = max(1, min(int(page or 1), PAGE_MAX))
    now = time.time() if now is None else now
    query = RULE
    words = _words(q)
    if words:
        query += " AND title:(" + " ".join(words) + ")"
    if genre:
        query += " AND subject:(" + " OR ".join(f'"{w}"' for w in GENRES[genre][1]) + ")"
    if span:
        query += f" AND year:[{span[0]} TO {span[1]}]"
    params = [("q", query), ("sort[]", LISTS[which]), ("rows", PAGE_SIZE), ("page", page), ("output", "json")]
    params += [("fl[]", f) for f in ("identifier", "title", "year", "runtime", "subject", "description",
                                     "external-identifier")]
    got = _cached("b:" + urllib.parse.urlencode(params), now, lambda: _get("/advancedsearch.php", params))
    resp = got.get("response") if isinstance(got.get("response"), dict) else {}
    docs = [d for d in resp.get("docs") or [] if isinstance(d, dict)]
    with ThreadPoolExecutor(max_workers=6) as pool:
        items = [i for i in pool.map(lambda d: _item(d, now), docs) if i]
    found = resp.get("numFound")
    more = isinstance(found, int) and page * PAGE_SIZE < found and page < PAGE_MAX
    me = T.signed_in()
    matched = [i for i in items if i["ids"].get("trakt")]
    if me and matched:
        try:
            seen = T._watched_ids("movie", now)
        except (T.TraktError, ValueError):
            seen = set()
        for i in matched:
            i["watched"] = i["ids"]["trakt"] in seen
        T._mark_watchlist("movie", matched, now)
        if hide_watched:
            items = [i for i in items if not i.get("watched")]
    from . import tmdb
    if tmdb.set_up() and matched:
        try:
            tmdb.add_posters(matched)  # a real poster where there is one; else the Archive's frame
        except Exception:  # noqa: BLE001
            pass
    return {"kind": "movie", "list": which, "lists": list(LISTS), "page": page, "more": more,
            "hide_watched": bool(hide_watched and me), "signed_in": me, "items": items, "q": " ".join(words),
            "genres": [{"slug": s, "name": n} for s, (n, _w) in GENRES.items()],
            "source": {"id": "archive", **SOURCES["archive"]}, "attribution": ATTRIBUTION}


def _allowed(md: dict) -> bool:
    """The rule, checked on an item's own metadata (never trusted from a request)."""
    meta = md.get("metadata") if isinstance(md.get("metadata"), dict) else {}
    cols = meta.get("collection")
    cols = cols if isinstance(cols, list) else [cols]
    return (meta.get("mediatype") == "movies" and "feature_films" in cols
            and bool(_LICENCE.match(str(_first(meta.get("licenseurl")) or ""))) and not md.get("is_dark"))


def _seconds(v) -> float:
    s = str(v or "")
    try:
        if ":" in s:
            total = 0.0
            for part in s.split(":"):
                total = total * 60 + float(part)
            return total
        return float(s)
    except ValueError:
        return 0.0


def _video(files: list) -> Optional[dict]:
    """The item's film: its longest MP4 the phone plays, the best format of
    those. Nothing else is ever offered: not the item's torrent, not an
    original in another container, not a private file."""
    ok = []
    for f in files:
        if not isinstance(f, dict):
            continue
        name, fmt = str(f.get("name") or ""), str(f.get("format") or "").lower()
        if not name.lower().endswith(".mp4") or fmt not in VIDEO_FORMATS or f.get("private") in (True, "true"):
            continue
        if ".." in name or name.startswith("/"):
            continue
        ok.append((f, _seconds(f.get("length")), VIDEO_FORMATS.index(fmt)))
    if not ok:
        return None
    longest = max(s for _, s, _ in ok)
    best = min((x for x in ok if x[1] >= longest * 0.98), key=lambda x: x[2])
    return best[0]


def _subtitles(files: list) -> list[dict]:
    out = []
    for f in files:
        name = str(f.get("name") or "") if isinstance(f, dict) else ""
        low = name.lower()
        if not low.endswith((".srt", ".vtt")) or ".asr." in low or ".." in name:
            continue
        # A language the name ends in ("film.fr.srt", "film_eng.srt"); else taken as English.
        m = re.search(r"[._-]([a-z]{2,3})\.(srt|vtt)$", low)
        code = {"eng": "en", "fre": "fr", "fra": "fr", "ger": "de", "deu": "de", "spa": "es", "ita": "it"}.get(
            m.group(1), m.group(1) if len(m.group(1)) == 2 else "") if m else ""
        out.append({"name": name, "lang": code or "en", "label": code.upper() if code else "Subtitles"})
    return out[:8]


def _find(title: str, year, now: float) -> Optional[str]:
    """The Archive's item for a title and year: the most downloaded one whose title is the same."""
    words, y = _words(title), _year(year)
    if not words or not y:
        return None
    params = [("q", f"{RULE} AND title:({' '.join(words)}) AND year:[{y - 1} TO {y + 1}]"),
              ("sort[]", "downloads desc"), ("rows", 10), ("page", 1), ("output", "json"),
              ("fl[]", "identifier"), ("fl[]", "title"), ("fl[]", "year")]
    got = _cached("f:" + urllib.parse.urlencode(params), now, lambda: _get("/advancedsearch.php", params))
    resp = got.get("response") if isinstance(got.get("response"), dict) else {}
    for d in resp.get("docs") or []:
        if isinstance(d, dict) and _IDENT.fullmatch(str(d.get("identifier") or "")) \
                and _fold(_title(d.get("title"))) == _fold(title):
            return str(d["identifier"])
    return None


def _metadata(ident: str, now: float) -> dict:
    if not _IDENT.fullmatch(str(ident or "")):
        raise ValueError("id is one of the source's item ids")
    md = _cached(f"m:{ident}", now, lambda: _get(f"/metadata/{ident}"))
    if not _allowed(md):
        raise LookupError("no free film by that id")
    return md


def file(ident: str = "", title: str = "", year="", now: Optional[float] = None) -> dict:
    """What GET /watch/free/file answers (less `ok`): a film's playable
    file, by the Archive's item id, or found for a title and year.
    ValueError for a bad ask; LookupError when there is no such free film
    or it has no file the phone plays."""
    now = time.time() if now is None else now
    ident = str(ident or "").strip()
    if not ident:
        if not str(title or "").strip() or not _year(year):
            raise ValueError("which film: id, or title and year")
        ident = _find(str(title), year, now) or ""
        if not ident:
            raise LookupError("no free film of that title and year")
    md = _metadata(ident, now)
    files = md.get("files") if isinstance(md.get("files"), list) else []
    f = _video(files)
    if not f:
        raise LookupError("that film has no file this app plays")
    meta = md["metadata"]
    quoted = urllib.parse.quote(str(f["name"]), safe="/")
    size = str(f.get("size") or "")
    height = str(f.get("height") or "")
    return {"source": "archive", "id": ident, "title": _title(meta.get("title")), "year": _year(meta.get("year")),
            "url": f"{HOST}/download/{urllib.parse.quote(ident)}/{quoted}", "mime": "video/mp4",
            "duration": round(_seconds(f.get("length"))) or None,
            "size": int(size) if size.isdigit() else None, "height": int(height) if height.isdigit() else None,
            "page": f"{HOST}/details/{ident}", "license": str(_first(meta.get("licenseurl")) or ""),
            "subtitles": _subtitles(files), "attribution": ATTRIBUTION}


def to_vtt(text: str) -> str:
    """SubRip as WebVTT (a WebVTT file is given back as it is)."""
    text = str(text or "").replace("\r\n", "\n").replace("\r", "\n").lstrip("﻿")
    if text.lstrip().startswith("WEBVTT"):
        return text
    def stamp(m):
        return f"{m.group(1)}.{(m.group(2) + '00')[:3]}"
    return "WEBVTT\n\n" + re.sub(r"(\d{1,2}:\d{2}:\d{2}),(\d{1,3})", stamp, text).strip() + "\n"


def subtitles(ident: str, name: str, now: Optional[float] = None) -> dict:
    """What GET /watch/free/subtitles answers: `{"vtt", "lang", "label"}`
    for one of the subtitle files `file()` listed for the item."""
    now = time.time() if now is None else now
    md = _metadata(str(ident or "").strip(), now)
    row = next((s for s in _subtitles(md.get("files") if isinstance(md.get("files"), list) else [])
                if s["name"] == name), None)
    if not row:
        raise LookupError("that film has no such subtitles")
    path = f"/download/{urllib.parse.quote(ident)}/{urllib.parse.quote(row['name'], safe='/')}"
    return {"vtt": _cached(f"v:{path}", now, lambda: to_vtt(_get_text(path))), "lang": row["lang"], "label": row["label"]}
