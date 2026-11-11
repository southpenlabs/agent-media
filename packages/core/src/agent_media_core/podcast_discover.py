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
LISTS = ("top", "following", "new")
FEED_TTL_S = 3600.0
FOLLOWED_NEWEST = 3
FOLLOWED_MAX = 15

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

def _genre_id(r: dict) -> Optional[str]:
    return next((str(g) for g in r.get("genreIds") or [] if str(g) in GENRES), None)


def _show_row(r: dict) -> dict:
    cid = r.get("collectionId") or r.get("trackId")
    return {"id": f"itunes:{cid}", "itunes": str(cid) if cid else None, "genre_id": _genre_id(r),
            "title": str(r.get("collectionName") or r.get("trackName") or ""),
            "author": r.get("artistName") or None, "feed": r.get("feedUrl") or None,
            "art": r.get("artworkUrl600") or r.get("artworkUrl100") or None,
            "genre": r.get("primaryGenreName") or None, "episodes": r.get("trackCount") or None,
            "link": r.get("collectionViewUrl") or None}


def _itunes(params: dict) -> list[dict]:
    url = f"{SEARCH}?{urllib.parse.urlencode({'media': 'podcast', 'country': region(), **params})}"
    code, got = _get(url)
    if code != 200 or not isinstance(got, dict):
        raise OSError(f"Apple Podcasts answered HTTP {code}")
    return [r for r in got.get("results") or [] if isinstance(r, dict)]


def search(q: str, now: float) -> list[dict]:
    q = " ".join(str(q or "").split())[:200]
    if len(q) < 2:
        raise ValueError("search needs q, two letters or more")
    return _cached(f"s:{region()}:{q.lower()}", lambda: [
        _show_row(r) for r in _itunes({"term": q, "entity": "podcast", "limit": 50})
        if r.get("collectionId") or r.get("trackId")], now)


def _episode_row(r: dict) -> dict:
    return {"id": f"ep:{r.get('trackId')}", "title": str(r.get("trackName") or ""),
            "show": r.get("collectionName") or None, "itunes": str(r.get("collectionId") or "") or None,
            "feed": r.get("feedUrl") or None, "url": r.get("episodeUrl") or None,
            "published": r.get("releaseDate") or None,
            "duration_s": round(r["trackTimeMillis"] / 1000) if isinstance(r.get("trackTimeMillis"), (int, float)) else None,
            "description": _text(r.get("description") or r.get("shortDescription") or "")[:1000] or None,
            "art": r.get("artworkUrl600") or r.get("artworkUrl160") or None}


def search_episodes(q: str, now: float) -> list[dict]:
    """Single episodes by words (entity=podcastEpisode), kept an hour."""
    q = " ".join(str(q or "").split())[:200]
    return _cached(f"se:{region()}:{q.lower()}", lambda: [
        _episode_row(r) for r in _itunes({"term": q, "entity": "podcastEpisode", "limit": 25})
        if r.get("episodeUrl")], now)


def by_author(name: str, now: float) -> list[dict]:
    """A host's shows: Apple's directory by author, those whose author is the name."""
    name = " ".join(str(name or "").split())[:200]
    if len(name) < 2:
        raise ValueError("an author's name, two letters or more")

    def ask():
        rows = [_show_row(r) for r in _itunes({"term": name, "entity": "podcast", "attribute": "authorTerm", "limit": 50})
                if r.get("collectionId")]
        same = [r for r in rows if (r.get("author") or "").strip().lower() == name.lower()]
        return same or rows
    return _cached(f"au:{region()}:{name.lower()}", ask, now)


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


_TAGS = re.compile(r"<[^>]+>")


def _text(html: str) -> str:
    import html as H
    return " ".join(H.unescape(_TAGS.sub(" ", str(html or ""))).split())


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _seconds(d: str) -> Optional[int]:
    d = str(d or "").strip()
    if not d:
        return None
    try:
        parts = [float(x) for x in d.split(":")]
    except ValueError:
        return None
    total = 0.0
    for x in parts:
        total = total * 60 + x
    return int(total)


def _when(s: str) -> Optional[float]:
    from email.utils import parsedate_to_datetime
    try:
        return parsedate_to_datetime(s).timestamp()
    except (TypeError, ValueError, IndexError):
        try:
            from datetime import datetime
            return datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp()
        except ValueError:
            return None


def parse(xml: bytes) -> dict:
    """A feed's show and every episode with audio, newest first."""
    import xml.etree.ElementTree as ET
    root = ET.fromstring(xml)
    ch = next((e for e in root.iter() if _local(e.tag) == "channel"), root)
    show_: dict = {"title": "", "author": None, "description": None, "link": None, "art": None, "categories": []}
    eps = []
    for c in ch:
        n = _local(c.tag)
        if n == "title" and not show_["title"]:
            show_["title"] = (c.text or "").strip()
        elif n == "author" and not show_["author"]:
            show_["author"] = (c.text or "").strip() or None
        elif n in ("summary", "description") and not show_["description"]:
            show_["description"] = _text(c.text)[:2000] or None
        elif n == "link" and not show_["link"] and (c.text or "").startswith("http"):
            show_["link"] = c.text.strip()
        elif n == "image":
            href = c.get("href") or next((x.text for x in c if _local(x.tag) == "url"), None)
            if href and not show_["art"]:
                show_["art"] = href.strip()
        elif n == "category" and c.get("text"):
            for name in [c.get("text")] + [x.get("text") for x in c if _local(x.tag) == "category" and x.get("text")]:
                if name not in show_["categories"]:
                    show_["categories"].append(name)
        elif n == "item":
            ep = {"title": "", "url": "", "published": None, "at": None, "duration_s": None, "description": None}
            for f in c:
                fn = _local(f.tag)
                if fn == "title":
                    ep["title"] = (f.text or "").strip()
                elif fn == "enclosure" and (f.get("type", "").startswith("audio") or not f.get("type")):
                    ep["url"] = f.get("url", "")
                elif fn == "pubDate":
                    ep["published"] = (f.text or "").strip() or None
                    ep["at"] = _when(ep["published"] or "")
                elif fn == "duration":
                    ep["duration_s"] = _seconds(f.text or "")
                elif fn in ("summary", "description") and not ep["description"]:
                    ep["description"] = _text(f.text)[:1500] or None
            if ep["url"].startswith(("http://", "https://")):
                eps.append(ep)
    eps.sort(key=lambda e: -(e["at"] or 0))
    return {"show": show_, "episodes": eps}


def _fetch_feed(url: str) -> bytes:
    import urllib.request
    req = urllib.request.Request(url, headers={"User-Agent": "agent-media/1 ( https://github.com/southpenlabs/agent-media )"})
    with urllib.request.urlopen(req, timeout=20) as r:   # noqa: S310 — http(s) only, checked
        return r.read(20 * 1024 * 1024)


def feed(url: str, now: Optional[float] = None) -> dict:
    url = _feed_ok(url)
    return _cached(f"f:{url}", lambda: parse(_fetch_feed(url)), time.time() if now is None else now, FEED_TTL_S)


def _new_followed(now: float) -> list[dict]:
    """The newest episodes of the shows followed, newest first."""
    out = []
    for f in follows()[:FOLLOWED_MAX]:
        try:
            got = feed(f["feed"], now)
        except Exception:  # noqa: BLE001 — that show left out
            continue
        for e in got["episodes"][:FOLLOWED_NEWEST]:
            out.append({**e, "show": f.get("title") or got["show"]["title"], "feed": f["feed"],
                        "art": f.get("art") or got["show"].get("art")})
    out.sort(key=lambda e: -(e.get("at") or 0))
    return out


def listing(which: str, q: str = "", genre: str = "", page: int = 1, now: Optional[float] = None) -> dict:
    """What GET /podcasts/discover answers (less `ok`)."""
    now = time.time() if now is None else now
    if which not in LISTS + ("search", "author"):
        raise ValueError(f"list must be one of {', '.join(LISTS)}, search or author")
    genres = [{"id": k, "name": v} for k, v in sorted(GENRES.items(), key=lambda x: x[1])]
    page = max(1, page)
    start = (page - 1) * PAGE
    if which == "new":
        eps = _new_followed(now)
        return {"list": which, "lists": list(LISTS), "page": page, "more": len(eps) > start + PAGE, "items": [],
                "episodes": eps[start:start + PAGE], "genres": genres}
    episodes = None
    if which == "search":
        rows = search(q, now)
        try:
            episodes = search_episodes(q, now)[:10]
        except Exception:  # noqa: BLE001 — the shows without episodes
            episodes = []
    elif which == "author":
        rows = by_author(q, now)
    elif which == "top":
        rows = top(genre, now)
    else:
        rows = [{"id": f"feed:{r['feed']}", "itunes": r.get("itunes"), "title": r.get("title") or r["feed"],
                 "author": r.get("author"), "feed": r["feed"], "art": r.get("art"), "genre": None,
                 "episodes": None, "link": None} for r in follows()]
    followed = {r.get("feed") for r in follows()} | {r.get("itunes") for r in follows() if r.get("itunes")}
    rows = [{**r, "following": (r.get("feed") in followed) or (r.get("itunes") in followed)} for r in rows]
    return {"list": which, "lists": list(LISTS), "page": page, "more": len(rows) > start + PAGE,
            "items": rows[start:start + PAGE], "genres": genres,
            **({"q": q} if which in ("search", "author") else {}), **({"genre": genre} if genre else {}),
            **({"episodes": episodes} if episodes is not None else {})}


def _lookup(itunes: str, now: float) -> dict:
    if not str(itunes).isdigit():
        raise ValueError("itunes is a show's Apple id")

    def ask():
        code, got = _get(f"{LOOKUP}?id={itunes}&entity=podcast&country={region()}")
        if code != 200 or not isinstance(got, dict) or not got.get("results"):
            raise LookupError("Apple Podcasts has no such show")
        return _show_row(got["results"][0])
    return _cached(f"l:{itunes}", ask, now, 86400.0)


def show(itunes: str = "", feed_url: str = "", q: str = "", page: int = 1, now: Optional[float] = None) -> dict:
    """A show's page: `{show: {title, author, art, feed, itunes, genre,
    genre_id, categories, description, link, episodes, last_episode,
    every_days, following}, episodes: [...], page, more, q?}` — every episode
    of its feed, newest first, PAGE a page, those whose title or
    description has the words `q`."""
    now = time.time() if now is None else now
    meta: dict = {}
    if itunes:
        meta = _lookup(itunes, now)
        feed_url = meta.get("feed") or ""
        if not feed_url:
            raise LookupError("Apple Podcasts gives no feed for that show")
    got = feed(feed_url, now)
    eps = got["episodes"]
    kept = next((r for r in follows() if r.get("feed") == feed_url), None)
    ats = [e["at"] for e in eps[:11] if e.get("at")]
    gaps = sorted(a - b for a, b in zip(ats, ats[1:]) if a > b)
    every = round(gaps[len(gaps) // 2] / 86400.0, 1) if gaps else None
    sh = got["show"]
    words = " ".join(str(q or "").split()).lower()
    shown = [e for e in eps if not words or all(w in f"{e['title']} {e.get('description') or ''}".lower() for w in words.split())]
    page = max(1, page)
    start = (page - 1) * PAGE
    return {"show": {"title": meta.get("title") or sh["title"] or (kept or {}).get("title") or feed_url,
                     "author": meta.get("author") or sh.get("author") or (kept or {}).get("author"),
                     "art": meta.get("art") or sh.get("art") or (kept or {}).get("art"),
                     "feed": feed_url, "itunes": meta.get("itunes") or (kept or {}).get("itunes"),
                     "genre": meta.get("genre") or (sh["categories"][0] if sh["categories"] else None),
                     "genre_id": meta.get("genre_id"), "categories": sh["categories"],
                     "description": sh.get("description"), "link": sh.get("link") or meta.get("link"),
                     "episodes": len(eps), "last_episode": eps[0].get("published") if eps else None,
                     "every_days": every, "following": kept is not None},
            "episodes": shown[start:start + PAGE], "page": page, "more": len(shown) > start + PAGE,
            **({"q": words} if words else {})}


def episode(feed_url: str, url: str) -> dict:
    """One of the show's episodes (by its audio address, which must be in
    the feed) saved as a podcast link: `{"uri", "title", "show"}`."""
    from . import podcast_links

    got = feed(feed_url)
    ep = next((e for e in got["episodes"] if e.get("url") == url), None)
    if not ep:
        raise ValueError("that episode is not in the show's feed")
    kept = next((r for r in follows() if r.get("feed") == feed_url), None)
    title = (kept or {}).get("title") or got["show"]["title"]
    podcast_links.add_audio(url, title=ep.get("title") or "", show=title, art=got["show"].get("art"),
                            published=ep.get("published"), page=feed_url, duration_s=ep.get("duration_s"))
    return {"uri": url, "title": ep.get("title") or "", "show": title}
