"""Who uploaded what was played: a YouTube video's channel, and the
channel's own page.

David, 9 Nov 2026, of the channel VaniBorn (their songs on the automatic
"VaniBorn - Topic" channel, their DJ mixes on "vaniBorn"): a mix counted
only under its chapters' artists, so a VaniBorn search or group showed their
songs and not their mixes; and "Can we get to an info page for the channel
too?"

**A video's channel** is YouTube's public oEmbed answer (`author_name`,
`author_url`), which answers a datacenter address where the video itself
does not. The rooms cache's `.title` sidecar holds only the title, so each
video's channel is looked up once, in the background, a few a pass, and
kept in `<state_dir>/music-channels.json` (one not answered is asked again
a day later). `row(vid)` is what a Recently played row carries as `channel`.

**A channel's key** (`key`) is its name without " - Topic", accents and
case, and anything not a letter or digit: "vaniBorn", "VaniBorn - Topic"
and a chapter's "@VaniBorn" ("Vani Born") are one.

**A channel's page** (`page`) is what David played from it (songs and mixes,
from the whole history: rows whose channel, artist or a chapter's artist has
that key) and, where they can be had without credentials:

- its details (name, picture, banner, description, subscribers, videos,
  country, canonical address) from its About page, read by the personal-use
  add-on's `youtube-metadata` `channel(url)` (youtube_addon); none without it
- its latest uploads from YouTube's public RSS feed of the channel id
  (`/feeds/videos.xml?channel_id=UC…`), when the id is known (the add-on's
  canonical address, or an oEmbed `author_url` of the /channel/ form)

Both are kept a day in `<state_dir>/music-channel-pages.json`.
MEDIA_MUSIC_CHANNEL=0 turns every lookup off (pinned off in the tests'
conftests): what is kept is still answered.
"""

from __future__ import annotations

import calendar
import json
import logging
import os
import re
import threading
import time
import unicodedata
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from typing import Optional

from ._paths import state_dir

log = logging.getLogger(__name__)

_FILE = "music-channels.json"
_PAGES = "music-channel-pages.json"
#: oEmbed lookups per background pass.
_PER_PASS = 20
_GAP_S = 0.3
#: A video oEmbed did not answer is asked again after this long.
_RETRY_S = 86400.0
#: A channel's details and uploads are kept this long; a failed read an hour.
PAGE_TTL_S = 86400.0
_PAGE_MISS_S = 3600.0
#: Latest uploads shown on a channel's page.
UPLOADS = 15

_LOCK = threading.Lock()
_BUSY = threading.Event()
_VID = re.compile(r"^[A-Za-z0-9_-]{11}$")
_TOPIC = re.compile(r"\s+-\s+Topic$", re.I)
_CHANNEL_ID = re.compile(r"/channel/(UC[A-Za-z0-9_-]{22})")
_HANDLE = re.compile(r"/(@[\w.\-]+)")


def online() -> bool:
    """Whether lookups may go out (MEDIA_MUSIC_CHANNEL, on by default)."""
    return os.environ.get("MEDIA_MUSIC_CHANNEL", "1").strip() != "0"


def _load(name: str) -> dict:
    try:
        d = json.loads((state_dir() / name).read_text())
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(name: str, d: dict) -> None:
    try:
        path = state_dir() / name
        tmp = path.with_suffix(f".{os.getpid()}.{threading.get_ident()}.tmp")
        tmp.write_text(json.dumps(d))
        tmp.replace(path)
    except OSError:
        pass


# --- names ------------------------------------------------------------------------

def display(name: Optional[str]) -> str:
    """A channel's name as a person's: without " - Topic"."""
    return _TOPIC.sub("", (name or "").strip()).strip()


def is_topic(name: Optional[str]) -> bool:
    return bool(_TOPIC.search((name or "").strip()))


def fold(text: Optional[str]) -> str:
    """Lower case without accents: "Beyoncé" is "beyonce"."""
    t = unicodedata.normalize("NFKD", text or "")
    return "".join(c for c in t if not unicodedata.combining(c)).casefold()


def key(name: Optional[str]) -> str:
    """The key a channel and an artist of the same name share."""
    return re.sub(r"[\W_]", "", fold(display(name)))


def artists(credit: Optional[str]) -> list[str]:
    """The names in a chapter's credit: "A, B & C", "A feat. B"."""
    parts = re.split(r",|&| x |\bfeat\.?\s|\bft\.?\s|\bfeaturing\b|\band\b", credit or "", flags=re.I)
    return [p.strip() for p in parts if p.strip()]


# --- a video's channel ---------------------------------------------------------------

def _parts(url: str) -> dict:
    m = _CHANNEL_ID.search(url or "")
    h = _HANDLE.search(url or "")
    return {"id": m.group(1) if m else None, "handle": h.group(1) if h else None}


def known(vid: str) -> Optional[dict]:
    """`{"name", "url"}` oEmbed gave for a video, None when not known."""
    with _LOCK:
        e = _load(_FILE).get(vid)
    return e if isinstance(e, dict) and e.get("name") else None


def _row(e: dict) -> dict:
    name = str(e.get("name") or "")
    url = str(e.get("url") or "") or None
    return {"name": display(name), "uploader": name, "key": key(name), "url": url,
            "topic": is_topic(name), **_parts(url or "")}


def row(vid: str) -> Optional[dict]:
    """A Recently played row's `channel`: `{"name"` (without " - Topic"),
    `"uploader"` (as YouTube writes it), `"key"`, `"url"`, `"id"` (UC…) or
    `"handle"` (@…) where the address gives one, `"topic"}`; None until it
    is looked up."""
    e = known(vid)
    return _row(e) if e else None


def rows(vids: list[str]) -> dict:
    """`row` for many videos, the cache read once."""
    with _LOCK:
        d = _load(_FILE)
    out = {}
    for v in vids:
        e = d.get(v)
        if isinstance(e, dict) and e.get("name"):
            out[v] = _row(e)
    return out


def remember(vid: str, name: str, url: str = "") -> None:
    """Keep a video's channel learned elsewhere (yt-dlp's uploader)."""
    if not vid or not name:
        return
    with _LOCK:
        d = _load(_FILE)
        d[vid] = {"name": name, "url": url or None, "at": time.time()}
        _save(_FILE, d)


def _oembed(vid: str) -> dict:
    from . import music_recent

    return music_recent.oembed_data(vid)


def fill(vids: list[str]) -> None:
    """Look up, in the background, the channels of the videos in `vids`
    not yet known (or not answered a day ago)."""
    if _BUSY.is_set() or not online():
        return
    with _LOCK:
        d = _load(_FILE)
    now = time.time()
    todo = []
    for v in dict.fromkeys(vids):
        if not v or not _VID.match(v):
            continue
        e = d.get(v)
        if isinstance(e, dict) and (e.get("name") or now - float(e.get("at") or 0) < _RETRY_S):
            continue
        todo.append(v)
    if not todo:
        return
    _BUSY.set()
    threading.Thread(target=_fill, args=(todo[:_PER_PASS],), daemon=True).start()


def _fill(vids: list[str]) -> None:
    try:
        for v in vids:
            got = _oembed(v)
            name = str(got.get("author_name") or "").strip()
            with _LOCK:
                d = _load(_FILE)
                d[v] = {"name": name or None, "url": str(got.get("author_url") or "") or None,
                        "at": time.time()}
                _save(_FILE, d)
            time.sleep(_GAP_S)
    except Exception as e:  # noqa: BLE001 — a channel is a nicety
        log.info("music-channel: lookup failed: %s", e)
    finally:
        _BUSY.clear()


def lookup(vid: str) -> Optional[dict]:
    """A video's channel now: the cache, else oEmbed (and kept)."""
    got = row(vid)
    if got or not _VID.match(vid or "") or not online():
        return got
    d = _oembed(vid)
    name = str(d.get("author_name") or "").strip()
    if not name:
        return None
    remember(vid, name, str(d.get("author_url") or ""))
    return row(vid)


# --- a channel's page ------------------------------------------------------------------

def belongs(item: dict, k: str) -> bool:
    """Whether a Recently played row is that channel's or that artist's: its
    channel, its artist (not a mix's, which is its uploader's guess), or
    one of its chapters' artists has the key."""
    if not k:
        return False
    ch = item.get("channel") or {}
    if ch.get("key") == k:
        return True
    if not item.get("mix") and key(item.get("artist")) == k:
        return True
    return any(key(a) == k for c in item.get("chapters") or [] for a in artists(c.get("artist")))


def _details(url: str) -> Optional[dict]:
    """The channel's About page, read by the YouTube add-on; None without one."""
    from . import youtube_addon

    addon = youtube_addon.get("youtube-metadata")
    if addon is None or not hasattr(addon, "channel"):
        return None
    try:
        got = addon.channel(url) or {}
    except Exception:  # noqa: BLE001 — no page, no details
        return {}
    return {k: got.get(k) for k in ("name", "photo", "photo_large", "banner", "description",
                                    "subscribers", "videos", "country", "url")}


_ATOM = "{http://www.w3.org/2005/Atom}"
_YT = "{http://www.youtube.com/xml/schemas/2015}"
_MEDIA = "{http://search.yahoo.com/mrss/}"


def parse_feed(xml: bytes | str) -> list[dict]:
    """A channel's RSS feed as rows: `{"uri", "title", "art", "published"}`."""
    root = ET.fromstring(xml)
    out = []
    for e in root.findall(f"{_ATOM}entry"):
        vid = (e.findtext(f"{_YT}videoId") or "").strip()
        if not _VID.match(vid):
            continue
        published = e.findtext(f"{_ATOM}published") or ""
        try:
            at = float(calendar.timegm(time.strptime(published[:19], "%Y-%m-%dT%H:%M:%S")))
        except ValueError:
            at = 0.0
        out.append({"id": f"up:{vid}", "uri": f"yt:{vid}", "title": (e.findtext(f"{_ATOM}title") or "").strip() or None,
                    "art": f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg", "published": at, "at": at,
                    "kind": "music", "session": None, "inferred": False})
    return out[:UPLOADS]


def _feed(channel_id: str) -> bytes:
    url = "https://www.youtube.com/feeds/videos.xml?channel_id=" + urllib.parse.quote(channel_id)
    req = urllib.request.Request(url, headers={"User-Agent": "agent-media/1.0"})
    with urllib.request.urlopen(req, timeout=8) as r:
        return r.read(2_000_000)


def _uploads(channel_id: str) -> Optional[list]:
    """The channel's latest uploads, None when the feed did not answer."""
    try:
        return parse_feed(_feed(channel_id))
    except Exception as e:  # noqa: BLE001 — no uploads, not a failure
        log.info("music-channel: feed of %s failed: %s", channel_id, e)
        return None


def _cached_page(url: str, fresh: bool = False) -> dict:
    """`{"details", "uploads"}` for a channel address, kept a day."""
    now = time.time()
    with _LOCK:
        e = _load(_PAGES).get(url)
    if isinstance(e, dict) and not fresh:
        ttl = PAGE_TTL_S if e.get("ok") else _PAGE_MISS_S
        if now - float(e.get("at") or 0) < ttl:
            return e
    if not online():
        return e if isinstance(e, dict) else {}
    details = _details(url)
    cid = _parts((details or {}).get("url") or "")["id"] or _parts(url)["id"]
    uploads = _uploads(cid) if cid else None
    e = {"details": details, "uploads": uploads, "id": cid, "at": now,
         "ok": bool(details) or uploads is not None}
    with _LOCK:
        d = _load(_PAGES)
        d[url] = e
        _save(_PAGES, d)
    return e


def _pick_url(played: list[dict], k: str) -> Optional[str]:
    """The channel's address: the one most of its plays came from, an
    uploader's own before its automatic Topic channel."""
    count: dict = {}
    for r in played:
        ch = r.get("channel") or {}
        if ch.get("key") == k and ch.get("url"):
            n = count.setdefault(ch["url"], [0, ch.get("topic")])
            n[0] += 1
    if not count:
        return None
    return sorted(count.items(), key=lambda kv: (bool(kv[1][1]), -kv[1][0]))[0][0]


def _name(played: list[dict], k: str, fallback: str) -> str:
    """How to write the name: the catalogue's or the Topic channel's way
    first ("VaniBorn"), then a song's artist, then the uploader's."""
    best = (-1, fallback)
    for r in played:
        ch = r.get("channel") or {}
        cand = []
        if not r.get("mix") and key(r.get("artist")) == k:
            # An artist the uploader's own channel gave is only its name again.
            own = ch.get("key") == k and not ch.get("topic")
            cand.append((3 if ch.get("topic") else 0 if own else 2, r.get("artist")))
        for c in r.get("chapters") or []:
            for a in artists(c.get("artist")):
                if key(a) == k:
                    cand.append((1, a))
        if ch.get("key") == k:
            cand.append((0, ch.get("name")))
        for rank, n in cand:
            if n and rank > best[0]:
                best = (rank, n)
    return best[1]


def page(name: str = "", url: str = "", vid: str = "", store=None, fresh: bool = False) -> dict:
    """A channel's page: `{"name", "key", "url", "youtube", "details",
    "played", "uploads", "sources"}`. Found by `name` (a channel or an
    artist), `url` (its address) or `vid` (a video of its)."""
    from . import music_recent

    ch = lookup(vid) if vid else None
    k = key(name) if name else ""
    if ch and (not k or ch["key"] == k):
        k, url = ch["key"], url or ch.get("url") or ""
    all_rows = music_recent.history(store=store)
    if url and not k:
        k = next((r["channel"]["key"] for r in all_rows
                  if (r.get("channel") or {}).get("url") == url), "")
    played = [r for r in all_rows if belongs(r, k)] if k else []
    url = url or _pick_url(played, k) or ""
    got = _cached_page(url, fresh) if url.startswith("https://www.youtube.com/") else {}
    details = got.get("details") or None
    uploads = got.get("uploads") or []
    shown = _name(played, k, display((details or {}).get("name")) or display(name)
                  or (ch or {}).get("name") or "")
    youtube = (details or {}).get("url") or url or (
        "https://www.youtube.com/results?search_query=" + urllib.parse.quote(shown) if shown else None)
    music_recent.fill_rows(played[:40])
    return {"name": shown, "key": k, "url": url or None, "youtube": youtube,
            "details": details, "played": played, "uploads": uploads,
            "sources": {"details": "page" if details else None,
                        "uploads": "feed" if got.get("uploads") is not None else None}}
