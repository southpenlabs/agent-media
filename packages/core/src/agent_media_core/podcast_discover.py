"""Podcasts to find and follow, beyond what was played (server-contract.md
§6.9a "Podcasts: search, browse, follow"; David, 11 Nov 2026: "Can we also
do search and browse for everything?").

  search(q)      Apple Podcasts' directory (the public iTunes Search API,
                 media=podcast, in MEDIA_MUSIC_REGION), kept an hour
  top(genre)     Apple's top podcasts chart for the region (iTunes' RSS,
                 which takes a genre; its newer RSS does not), kept an hour
  show(...)      a show by its Apple id (its feed looked up) or its feed:
                 the feed's newest episodes (practice_library.read_feed,
                 kept six hours)
  follow/unfollow  the shows followed (`<state_dir>/podcast-follows.json`):
                 the Podcasts tab's Following
  episode(...)   one of a show's episodes saved as a podcast link
                 (podcast_links.add_audio), so it plays as a saved episode
                 does and stays in Saved

`_get` is the one place that talks HTTP (the feed through read_feed).
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
import urllib.parse
from pathlib import Path
from typing import Optional

SEARCH = "https://itunes.apple.com/search"
LOOKUP = "https://itunes.apple.com/lookup"
TOP = "https://itunes.apple.com/{cc}/rss/toppodcasts/limit=100{genre}/json"
TTL_S = 3600.0
PAGE = 20
EPISODES = 20
#: Apple Podcasts' top-level genres, by id.
GENRES = {"1301": "Arts", "1321": "Business", "1303": "Comedy", "1304": "Education", "1483": "Fiction",
          "1512": "Health & Fitness", "1487": "History", "1305": "Kids & Family", "1502": "Leisure",
          "1310": "Music", "1489": "News", "1314": "Religion & Spirituality", "1533": "Science",
          "1324": "Society & Culture", "1545": "Sports", "1318": "Technology", "1488": "True Crime",
          "1309": "TV & Film"}
LISTS = ("top", "following")

_lock = threading.Lock()
_cache: dict[str, tuple[float, object]] = {}


def region() -> str:
    return ((os.environ.get("MEDIA_MUSIC_REGION") or "").strip() or "AU").lower()


def _get(url: str, timeout: float = 15.0) -> tuple[int, object]:
    import urllib.error
    import urllib.request
    req = urllib.request.Request(url, headers={"User-Agent": "agent-media/1 ( https://github.com/southpenlabs/agent-media )",
                                               "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode() or "null")
    except urllib.error.HTTPError as e:
        return e.code, None
    except ValueError:
        return 200, None


def clear_cache() -> None:
    with _lock:
        _cache.clear()


def _cached(key: str, fn, now: float, ttl: float = TTL_S):
    with _lock:
        hit = _cache.get(key)
    if hit and now - hit[0] < ttl:
        return hit[1]
    v = fn()
    with _lock:
        _cache[key] = (now, v)
    return v


# ---- following ------------------------------------------------------------------------------

def _follows_path() -> Path:
    from ._paths import state_dir
    return state_dir() / "podcast-follows.json"


def follows() -> list[dict]:
    try:
        d = json.loads(_follows_path().read_text())
        return d if isinstance(d, list) else []
    except (OSError, ValueError):
        return []


def _save_follows(rows: list[dict]) -> None:
    p = _follows_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(f".{p.name}.{os.getpid()}")
    tmp.write_text(json.dumps(rows))
    os.replace(tmp, p)


def _feed_ok(feed: str) -> str:
    feed = str(feed or "").strip()
    if not feed.startswith(("http://", "https://")) or len(feed) > 1000:
        raise ValueError("feed must be the show's http(s) feed address")
    return feed


def follow(feed: str, title: str = "", author: str = "", art: str = "", itunes: str = "") -> list[dict]:
    feed = _feed_ok(feed)
    rows = [r for r in follows() if r.get("feed") != feed]
    rows.insert(0, {"feed": feed, "title": str(title or "")[:300], "author": str(author or "")[:200] or None,
                    "art": str(art or "")[:500] or None, "itunes": str(itunes or "") or None, "added": time.time()})
    _save_follows(rows)
    return rows


def unfollow(feed: str) -> list[dict]:
    feed = _feed_ok(feed)
    rows = [r for r in follows() if r.get("feed") != feed]
    _save_follows(rows)
    return rows


# ---- the directory --------------------------------------------------------------------------

def _show_row(r: dict) -> dict:
    cid = r.get("collectionId") or r.get("trackId")
    return {"id": f"itunes:{cid}", "itunes": str(cid) if cid else None,
            "title": str(r.get("collectionName") or r.get("trackName") or ""),
            "author": r.get("artistName") or None, "feed": r.get("feedUrl") or None,
            "art": r.get("artworkUrl600") or r.get("artworkUrl100") or None,
            "genre": r.get("primaryGenreName") or None, "episodes": r.get("trackCount") or None,
            "link": r.get("collectionViewUrl") or None}


def search(q: str, now: float) -> list[dict]:
    q = " ".join(str(q or "").split())[:200]
    if len(q) < 2:
        raise ValueError("search needs q, two letters or more")

    def ask():
        url = f"{SEARCH}?{urllib.parse.urlencode({'media': 'podcast', 'term': q, 'country': region(), 'limit': 50})}"
        code, got = _get(url)
        if code != 200 or not isinstance(got, dict):
            raise OSError(f"Apple Podcasts answered HTTP {code}")
        return [_show_row(r) for r in got.get("results") or [] if isinstance(r, dict) and (r.get("collectionId") or r.get("trackId"))]
    return _cached(f"s:{region()}:{q.lower()}", ask, now)


def top(genre: str, now: float) -> list[dict]:
    if genre and genre not in GENRES:
        raise ValueError("genre is one of the genres' ids")

    def ask():
        code, got = _get(TOP.format(cc=region(), genre=f"/genre={genre}" if genre else ""))
        if code != 200 or not isinstance(got, dict):
            raise OSError(f"Apple's chart answered HTTP {code}")
        entries = (got.get("feed") or {}).get("entry") or []
        if isinstance(entries, dict):
            entries = [entries]
        out = []
        for e in entries:
            if not isinstance(e, dict):
                continue
            iid = ((e.get("id") or {}).get("attributes") or {}).get("im:id")
            imgs = e.get("im:image") or []
            art = imgs[-1].get("label") if imgs and isinstance(imgs[-1], dict) else None
            if art:
                art = re.sub(r"/\d+x\d+bb\.(png|jpg)$", "/600x600bb.jpg", art)
            out.append({"id": f"itunes:{iid}", "itunes": iid, "title": str((e.get("im:name") or {}).get("label") or ""),
                        "author": (e.get("im:artist") or {}).get("label") or None, "feed": None, "art": art,
                        "genre": ((e.get("category") or {}).get("attributes") or {}).get("label") or None,
                        "episodes": None, "link": ((e.get("link") or {}).get("attributes") or {}).get("href")
                        if isinstance(e.get("link"), dict) else None})
        return [r for r in out if r["itunes"] and r["title"]]
    return _cached(f"t:{region()}:{genre}", ask, now)


def _lookup(itunes: str, now: float) -> dict:
    if not str(itunes).isdigit():
        raise ValueError("itunes is a show's Apple id")

    def ask():
        code, got = _get(f"{LOOKUP}?id={itunes}&entity=podcast")
        if code != 200 or not isinstance(got, dict) or not got.get("results"):
            raise LookupError("Apple Podcasts has no such show")
        return _show_row(got["results"][0])
    return _cached(f"l:{itunes}", ask, now, 86400.0)


def listing(which: str, q: str = "", genre: str = "", page: int = 1, now: Optional[float] = None) -> dict:
    """What GET /podcasts/discover answers (less `ok`)."""
    now = time.time() if now is None else now
    if which not in LISTS + ("search",):
        raise ValueError(f"list must be one of {', '.join(LISTS)}, or search")
    if which == "search":
        rows = search(q, now)
    elif which == "top":
        rows = top(genre, now)
    else:
        rows = [{"id": f"feed:{r['feed']}", "itunes": r.get("itunes"), "title": r.get("title") or r["feed"],
                 "author": r.get("author"), "feed": r["feed"], "art": r.get("art"), "genre": None,
                 "episodes": None, "link": None} for r in follows()]
    followed = {r.get("feed") for r in follows()} | {r.get("itunes") for r in follows() if r.get("itunes")}
    rows = [{**r, "following": (r.get("feed") in followed) or (r.get("itunes") in followed)} for r in rows]
    page = max(1, page)
    start = (page - 1) * PAGE
    return {"list": which, "lists": list(LISTS), "page": page, "more": len(rows) > start + PAGE,
            "items": rows[start:start + PAGE], "genres": [{"id": k, "name": v} for k, v in sorted(GENRES.items(), key=lambda x: x[1])],
            **({"q": q} if which == "search" else {}), **({"genre": genre} if genre else {})}


def show(itunes: str = "", feed: str = "", now: Optional[float] = None) -> dict:
    """A show and its newest episodes: `{show: {title, author, art, feed,
    itunes, genre, following}, episodes: [{title, url, published, duration}]}`."""
    from . import practice_library

    now = time.time() if now is None else now
    meta: dict = {}
    if itunes:
        meta = _lookup(itunes, now)
        feed = meta.get("feed") or ""
        if not feed:
            raise LookupError("Apple Podcasts gives no feed for that show")
    feed = _feed_ok(feed)
    got = practice_library.read_feed(feed)
    if not got:
        raise OSError("couldn't read that show's feed")
    eps = got.get("episodes") or []
    kept = next((r for r in follows() if r.get("feed") == feed), None)
    return {"show": {"title": meta.get("title") or got.get("title") or (kept or {}).get("title") or feed,
                     "author": meta.get("author") or (kept or {}).get("author"), "art": meta.get("art") or (kept or {}).get("art"),
                     "feed": feed, "itunes": meta.get("itunes") or (kept or {}).get("itunes"), "genre": meta.get("genre"),
                     "link": meta.get("link"), "following": kept is not None},
            "episodes": eps[:EPISODES]}


def episode(feed: str, url: str) -> dict:
    """One of the show's episodes (by its audio address, which must be in
    the feed) saved as a podcast link: `{"uri", "title", "show"}`."""
    from . import podcast_links

    got = show(feed=feed)
    ep = next((e for e in got["episodes"] if e.get("url") == url), None)
    if not ep:
        raise ValueError("that episode is not in the show's feed")
    podcast_links.add_audio(url, title=ep.get("title") or "", show=got["show"]["title"], art=got["show"].get("art"),
                            published=ep.get("published") or None, page=feed)
    return {"uri": url, "title": ep.get("title") or "", "show": got["show"]["title"]}
