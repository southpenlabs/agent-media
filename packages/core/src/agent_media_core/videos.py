"""The Videos tab: one search across several video sites (server-contract
§6.70; docs/proposals/2026-11-25-videos-tab.md, David, 25 Nov 2026:
"maybe we can just have a videos section that searches multiple video
sites", and "Can we also include Odyssee and Rumble?").

Sources, each asked in parallel with its own time limit (`LIMIT_S`):

- ``yt``: YouTube, read by the personal-use add-on (youtube_addon
  "youtube-metadata" ``search_videos(url, start, end, timeout)``: yt-dlp
  ``--flat-playlist -J`` on a results URL whose ``sp=`` carries the
  filters). Offered only where the add-on is installed and allowed.
- ``peertube``: PeerTube instances through Sepia Search
  (sepiasearch.org/api/v1/search/videos), each video with its licence.
- ``archive``: the Internet Archive's advanced search, ``mediatype:movies``.
- ``dailymotion``: api.dailymotion.com/videos?search=.
- ``odysee``: Odysee's search index, lighthouse.odysee.tv/search.
- ``vimeo``: api.vimeo.com/videos?query=, only with MEDIA_VIMEO_TOKEN (its
  search refuses an unauthenticated ask); off the list without one.

Rumble is not searched: it has no public search, and its search page
refuses automated clients; it, and Vimeo without a token, come in by link
and followed channel (step 3).

`search()` answers ``{"items", "sources": [{id, ok, error?, next?,
ignored?}]}``: a slow or failed source is reported and its rows left out,
never holding up the rest; rows are interleaved by each source's rank. A
filter a source cannot apply is dropped for it and named in ``ignored``;
a type a source has none of (a channel on the Archive) gives none of its
rows rather than rows of another kind. Answers are kept `CACHE_S` per
source, query, filters and page (Vimeo's `CACHE_S_OF`, its limit being 50
asks a quarter hour); a source that answers 429 is reported failed and not
asked again until its reset.
"""

from __future__ import annotations

import base64
import calendar
import json
import re
import threading
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, wait
from typing import Any, Callable, Optional

from . import failures as _failures

LIMIT_S = 6.0
CACHE_S = 600.0
PER_PAGE = 12
MOST_PAGES = 20
USER_AGENT = "agent-media videos (https://github.com/southpenlabs/agent-media)"

LENGTHS = ("short", "medium", "long")
DATES = ("today", "week", "month", "year")
TYPES = ("video", "live", "playlist", "channel")
SORTS = ("relevance", "date", "views")
FILTERS = {"length": LENGTHS, "date": DATES, "type": TYPES, "sort": SORTS}
#: YouTube's own bounds: under 4 minutes, 4 to 20, over 20.
SHORT_S, LONG_S = 4 * 60, 20 * 60
_DAYS = {"today": 1, "week": 7, "month": 31, "year": 366}

#: (url, timeout, headers=None) → the parsed JSON.
Fetch = Callable[..., Any]


def _fetch_json(url: str, timeout: float, headers: Optional[dict] = None) -> Any:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json",
                                               **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read(4_000_000))


def _iso(s: Any) -> Optional[int]:
    """An ISO 8601 time ("2018-06-06T13:07:05.855Z") as Unix seconds."""
    if not isinstance(s, str) or len(s) < 10:
        return None
    try:
        return calendar.timegm(time.strptime(s[:19] if len(s) >= 19 else s[:10],
                                             "%Y-%m-%dT%H:%M:%S" if len(s) >= 19 else "%Y-%m-%d"))
    except ValueError:
        return None


def _int(v: Any) -> Optional[int]:
    try:
        return int(v) if v is not None and v != "" else None
    except (TypeError, ValueError):
        return None


def _length_ok(length: str, secs: Optional[int]) -> bool:
    if not length:
        return True
    if secs is None:
        return False
    return secs < SHORT_S if length == "short" else secs > LONG_S if length == "long" else SHORT_S <= secs <= LONG_S


def _item(source: str, id: str, url: str, title: Any, channel: dict, **more: Any) -> dict:
    out = {"source": source, "id": id, "url": url, "title": title or "",
           "channel": {k: v for k, v in channel.items() if v}, "kind": more.pop("kind", "video")}
    out.update({k: v for k, v in more.items() if v is not None and v != ""})
    return out


# ---- YouTube (the add-on) -------------------------------------------------

def _yt_addon() -> Any:
    from . import youtube_addon
    addon = youtube_addon.get("youtube-metadata")
    return addon if addon is not None and hasattr(addon, "search_videos") else None


def _varint(n: int) -> bytes:
    out = b""
    while True:
        b, n = n & 0x7F, n >> 7
        out += bytes([b | (0x80 if n else 0)])
        if not n:
            return out


def yt_sp(length: str = "", date: str = "", type: str = "", sort: str = "") -> str:
    """YouTube's ``sp=`` search filter, the protobuf its own filter menu
    builds: field 1 the sort, field 2 the filters (1 upload date, 2 type,
    3 duration, 8 live). "" when nothing is set."""
    inner = b""
    if date:
        inner += b"\x08" + _varint({"today": 2, "week": 3, "month": 4, "year": 5}[date])
    if type in ("video", "channel", "playlist"):
        inner += b"\x10" + _varint({"video": 1, "channel": 2, "playlist": 3}[type])
    if length:
        inner += b"\x18" + _varint({"short": 1, "long": 2, "medium": 3}[length])
    if type == "live":
        inner += b"\x40\x01"
    msg = b""
    if sort in ("date", "views"):
        msg += b"\x08" + _varint({"date": 2, "views": 3}[sort])
    if inner:
        msg += b"\x12" + _varint(len(inner)) + inner
    return base64.b64encode(msg).decode() if msg else ""


def _yt_thumb(e: dict) -> Optional[str]:
    th = [t for t in e.get("thumbnails") or [] if isinstance(t, dict) and t.get("url")]
    if th:
        url = str(th[-1]["url"])
        return "https:" + url if url.startswith("//") else url
    return None


def yt_rows(data: dict) -> list[dict]:
    """yt-dlp's flat JSON of a results page as VideoItems."""
    out = []
    for e in (data or {}).get("entries") or []:
        if not isinstance(e, dict) or not e.get("id"):
            continue
        url = str(e.get("url") or "")
        if e.get("ie_key") == "Youtube":
            live = e.get("live_status") in ("is_live", "is_upcoming")
            vid = str(e["id"])
            out.append(_item("yt", vid, f"https://www.youtube.com/watch?v={vid}", e.get("title"),
                             {"name": e.get("channel") or e.get("uploader"), "id": e.get("channel_id"),
                              "url": e.get("channel_url") or e.get("uploader_url")},
                             kind="live" if live else "video", duration_s=_int(e.get("duration")),
                             published=_int(e.get("timestamp") or e.get("release_timestamp")),
                             views=_int(e.get("view_count") or e.get("concurrent_view_count")),
                             thumb=_yt_thumb(e) or f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg",
                             embed=f"https://www.youtube.com/embed/{vid}", playable_audio=True))
        elif e.get("ie_key") == "YoutubeTab":
            kind = "playlist" if "list=" in url else "channel"
            out.append(_item("yt", str(e["id"]), url, e.get("title") or e.get("channel"),
                             {"name": e.get("channel") or e.get("uploader") or (e.get("title") if kind == "channel" else None),
                              "id": e.get("channel_id"), "url": e.get("channel_url")},
                             kind=kind, thumb=_yt_thumb(e), views=_int(e.get("view_count")),
                             count=_int(e.get("playlist_count"))))
    return out


def _yt(q: str, f: dict, page: int, fetch: Fetch, timeout: float, now: float) -> tuple[list, bool, list]:
    addon = _yt_addon()
    if addon is None:
        raise RuntimeError("YouTube is not offered here")
    sp = yt_sp(f.get("length", ""), f.get("date", ""), f.get("type", ""), f.get("sort", ""))
    url = "https://www.youtube.com/results?" + urllib.parse.urlencode({"search_query": q, **({"sp": sp} if sp else {})})
    start, end = (page - 1) * PER_PAGE + 1, page * PER_PAGE
    rows = yt_rows(addon.search_videos(url, start, end, timeout))
    return rows, len(rows) >= PER_PAGE, []


# ---- PeerTube (Sepia Search) ----------------------------------------------

def _since(date: str, now: float) -> float:
    return now - _DAYS[date] * 86400


def _peertube(q: str, f: dict, page: int, fetch: Fetch, timeout: float, now: float) -> tuple[list, bool, list]:
    if f.get("type") in ("playlist", "channel"):
        return [], False, ["type"]
    p: dict = {"search": q, "start": (page - 1) * PER_PAGE, "count": PER_PAGE, "nsfw": "false",
               "sort": {"date": "-publishedAt", "views": "-views"}.get(f.get("sort", ""), "-match")}
    if f.get("type"):
        p["isLive"] = "true" if f["type"] == "live" else "false"
    length = f.get("length")
    if length == "short":
        p["durationMax"] = SHORT_S
    elif length == "long":
        p["durationMin"] = LONG_S
    elif length == "medium":
        p["durationMin"], p["durationMax"] = SHORT_S, LONG_S
    if f.get("date"):
        p["startDate"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(_since(f["date"], now)))
    data = fetch("https://sepiasearch.org/api/v1/search/videos?" + urllib.parse.urlencode(p), timeout) or {}
    out = []
    for v in data.get("data") or []:
        if not isinstance(v, dict) or not v.get("url"):
            continue
        ch = v.get("channel") or v.get("account") or {}
        thumbs = sorted((t for t in v.get("thumbnails") or [] if t.get("fileUrl") and t.get("aspectRatio") != "1:1"),
                        key=lambda t: t.get("width") or 0)
        thumb = next((t["fileUrl"] for t in thumbs if (t.get("width") or 0) >= 480), None) \
            or (thumbs[-1]["fileUrl"] if thumbs else v.get("thumbnailUrl"))
        out.append(_item("peertube", str(v.get("uuid") or v.get("shortUUID") or v.get("id")), v["url"], v.get("name"),
                         {"name": ch.get("displayName") or ch.get("name"), "id": ch.get("name") and ch.get("host")
                          and f"{ch['name']}@{ch['host']}", "url": ch.get("url")},
                         kind="live" if v.get("isLive") else "video", duration_s=_int(v.get("duration")),
                         published=_iso(v.get("publishedAt")), views=_int(v.get("views")), thumb=thumb,
                         embed=v.get("embedUrl"), licence=(v.get("licence") or {}).get("label"),
                         host=ch.get("host")))
    total = _int(data.get("total")) or 0
    return out, page * PER_PAGE < total, []


# ---- The Internet Archive -------------------------------------------------

_RUNTIME = re.compile(r"^(?:(\d+):)?(\d+):(\d+)$")


def archive_runtime(s: Any) -> Optional[int]:
    """The Archive's `runtime` in seconds: "1:31:44", "51:56", "108 min"."""
    s = str(s or "").strip().lstrip("~").strip()
    m = _RUNTIME.match(s)
    if m:
        return int(m.group(1) or 0) * 3600 + int(m.group(2)) * 60 + int(m.group(3))
    m = re.match(r"^(\d+(?:\.\d+)?)\s*(min|minutes|m)\b", s)
    if m:
        return int(float(m.group(1)) * 60)
    m = re.match(r"^(\d+(?:\.\d+)?)\s*(hours?|h)\b", s)
    return int(float(m.group(1)) * 3600) if m else None


def licence_label(url: Any) -> Optional[str]:
    """A licence URL as its short name: "CC BY-NC 3.0", "Public domain"."""
    u = str(url or "").lower()
    if not u:
        return None
    if "publicdomain" in u:
        return "CC0" if "zero" in u else "Public domain"
    m = re.search(r"creativecommons\.org/licenses/([a-z-]+)/(\d(?:\.\d)?)", u)
    if m:
        return f"CC {m.group(1).upper()} {m.group(2)}"
    return None


def _archive(q: str, f: dict, page: int, fetch: Fetch, timeout: float, now: float) -> tuple[list, bool, list]:
    if f.get("type") in ("live", "playlist", "channel"):
        return [], False, ["type"]
    ignored = [k for k in ("length",) if f.get(k)]
    query = f"({q}) AND mediatype:movies"
    if f.get("date"):
        query += " AND publicdate:[{} TO null]".format(time.strftime("%Y-%m-%d", time.gmtime(_since(f["date"], now))))
    p: list = [("q", query), ("rows", PER_PAGE), ("page", page), ("output", "json")]
    p += [("fl[]", k) for k in ("identifier", "title", "creator", "publicdate", "downloads", "licenseurl", "runtime")]
    sort = {"date": "publicdate desc", "views": "downloads desc"}.get(f.get("sort", ""))
    if sort:
        p.append(("sort[]", sort))
    data = fetch("https://archive.org/advancedsearch.php?" + urllib.parse.urlencode(p), timeout) or {}
    resp = data.get("response") or {}
    out = []
    for d in resp.get("docs") or []:
        ident = d.get("identifier")
        if not ident:
            continue
        creator = d.get("creator")
        creator = ", ".join(creator) if isinstance(creator, list) else creator
        title = d.get("title")
        out.append(_item("archive", ident, f"https://archive.org/details/{ident}",
                         ", ".join(title) if isinstance(title, list) else title,
                         {"name": creator}, duration_s=archive_runtime(d.get("runtime")),
                         published=_iso(d.get("publicdate")), views=_int(d.get("downloads")),
                         thumb=f"https://archive.org/services/img/{ident}",
                         embed=f"https://archive.org/embed/{ident}", licence=licence_label(d.get("licenseurl"))))
    return out, page * PER_PAGE < (_int(resp.get("numFound")) or 0), ignored


# ---- Dailymotion ----------------------------------------------------------

_DM_FIELDS = ("id,title,url,duration,created_time,views_total,thumbnail_480_url,owner.screenname,"
              "owner.id,owner.url,embed_url,onair,allow_embed")


def _dailymotion(q: str, f: dict, page: int, fetch: Fetch, timeout: float, now: float) -> tuple[list, bool, list]:
    if f.get("type") in ("playlist", "channel"):
        return [], False, ["type"]
    p: dict = {"search": q, "limit": PER_PAGE, "page": page, "fields": _DM_FIELDS,
               "sort": {"date": "recent", "views": "visited"}.get(f.get("sort", ""), "relevance")}
    if f.get("type"):
        p["flags"] = "live_onair" if f["type"] == "live" else "no_live"
    length = f.get("length")
    if length == "short":
        p["shorter_than"] = SHORT_S // 60
    elif length == "long":
        p["longer_than"] = LONG_S // 60
    elif length == "medium":
        p["longer_than"], p["shorter_than"] = SHORT_S // 60, LONG_S // 60
    if f.get("date"):
        p["created_after"] = int(_since(f["date"], now))
    data = fetch("https://api.dailymotion.com/videos?" + urllib.parse.urlencode(p), timeout) or {}
    out = []
    for v in data.get("list") or []:
        if not v.get("id"):
            continue
        out.append(_item("dailymotion", str(v["id"]), v.get("url") or f"https://www.dailymotion.com/video/{v['id']}",
                         v.get("title"), {"name": v.get("owner.screenname"), "id": v.get("owner.id"),
                                          "url": v.get("owner.url")},
                         kind="live" if v.get("onair") else "video", duration_s=_int(v.get("duration")),
                         published=_int(v.get("created_time")), views=_int(v.get("views_total")),
                         thumb=v.get("thumbnail_480_url"), embed=v.get("embed_url") if v.get("allow_embed") else None))
    return out, bool(data.get("has_more")), []


# ---- Odysee (lighthouse) --------------------------------------------------

def _odysee(q: str, f: dict, page: int, fetch: Fetch, timeout: float, now: float) -> tuple[list, bool, list]:
    if f.get("type") in ("live", "playlist", "channel"):
        return [], False, ["type"]
    ignored = ["sort"] if f.get("sort") == "views" else []
    # lighthouse has no length filter: a page is asked a little larger and
    # cut here by each claim's own duration.
    size = PER_PAGE * (3 if f.get("length") else 1)
    p: dict = {"s": q, "size": size, "from": (page - 1) * size, "mediaType": "video", "nsfw": "false",
               "include": "channel,channel_claim_id,title,duration,release_time,thumbnail_url,view_cnt"}
    if f.get("sort") == "date":
        p["sort_by"] = "release_time"
    if f.get("date"):
        p["time_filter"] = {"today": "today", "week": "thisweek", "month": "thismonth", "year": "thisyear"}[f["date"]]
    data = fetch("https://lighthouse.odysee.tv/search?" + urllib.parse.urlencode(p), timeout) or []
    out = []
    for v in data if isinstance(data, list) else []:
        name, claim = v.get("name"), v.get("claimId")
        if not name or not claim or not _length_ok(f.get("length", ""), _int(v.get("duration"))):
            continue
        path = f"{urllib.parse.quote(name, safe='')}:{claim}"
        ch, ch_id = v.get("channel"), v.get("channel_claim_id")
        ch_url = f"https://odysee.com/{urllib.parse.quote(ch, safe='@')}:{ch_id}" if ch and ch_id else None
        out.append(_item("odysee", claim, f"https://odysee.com/{path}", v.get("title") or name,
                         {"name": ch, "id": ch_id, "url": ch_url}, duration_s=_int(v.get("duration")),
                         published=_iso(v.get("release_time")), views=_int(v.get("view_cnt")),
                         thumb=v.get("thumbnail_url"), embed=f"https://odysee.com/$/embed/{path}"))
    return out, len(data) >= size if isinstance(data, list) else False, ignored


# ---- Vimeo (only with the owner's token) ---------------------------------

def vimeo_token() -> str:
    """MEDIA_VIMEO_TOKEN: a free Vimeo developer app's access token. Without
    one Vimeo is not searched (its search refuses an unauthenticated ask,
    error 8003); its links and channels still come in through yt-dlp."""
    import os
    return os.environ.get("MEDIA_VIMEO_TOKEN", "").strip()


_VIMEO_FIELDS = ("uri,name,link,duration,created_time,release_time,pictures.sizes,user.name,user.link,"
                 "user.uri,stats.plays,player_embed_url,license,privacy.embed")
_VIMEO_LICENCE = {"by": "CC BY", "by-sa": "CC BY-SA", "by-nd": "CC BY-ND", "by-nc": "CC BY-NC",
                  "by-nc-sa": "CC BY-NC-SA", "by-nc-nd": "CC BY-NC-ND", "cc0": "CC0"}


def _vimeo(q: str, f: dict, page: int, fetch: Fetch, timeout: float, now: float) -> tuple[list, bool, list]:
    token = vimeo_token()
    if not token:
        raise RuntimeError("Vimeo needs MEDIA_VIMEO_TOKEN")
    if f.get("type") in ("live", "playlist", "channel"):
        return [], False, ["type"]
    p: dict = {"query": q, "per_page": PER_PAGE, "page": page, "fields": _VIMEO_FIELDS,
               "sort": {"date": "date", "views": "plays"}.get(f.get("sort", ""), "relevant")}
    if f.get("sort"):
        p["direction"] = "desc"
    data = fetch("https://api.vimeo.com/videos?" + urllib.parse.urlencode(p), timeout,
                 headers={"Authorization": f"bearer {token}",
                          "Accept": "application/vnd.vimeo.*+json;version=3.4"}) or {}
    out = []
    for v in data.get("data") or []:
        vid = str(v.get("uri") or "").rsplit("/", 1)[-1]
        if not vid or not v.get("link"):
            continue
        user = v.get("user") or {}
        sizes = sorted((s for s in (v.get("pictures") or {}).get("sizes") or [] if s.get("link")),
                       key=lambda s: s.get("width") or 0)
        thumb = next((s["link"] for s in sizes if (s.get("width") or 0) >= 480), sizes[-1]["link"] if sizes else None)
        embeddable = ((v.get("privacy") or {}).get("embed") or "public") == "public"
        out.append(_item("vimeo", vid, v["link"], v.get("name"),
                         {"name": user.get("name"), "id": str(user.get("uri") or "").rsplit("/", 1)[-1] or None,
                          "url": user.get("link")}, duration_s=_int(v.get("duration")),
                         published=_iso(v.get("release_time") or v.get("created_time")),
                         views=_int((v.get("stats") or {}).get("plays")), thumb=thumb,
                         embed=(v.get("player_embed_url") or f"https://player.vimeo.com/video/{vid}") if embeddable else None,
                         licence=_VIMEO_LICENCE.get(str(v.get("license") or ""))))
    ignored = [k for k in ("length", "date") if f.get(k)]
    return out, bool((data.get("paging") or {}).get("next")), ignored


# ---- The table and the search ---------------------------------------------

#: id → (name, site, the filters it can apply, its search). A type it has
#: none of (TYPES_OF) gives none of its rows, with "type" in `ignored`.
SOURCES: dict[str, tuple[str, str, tuple, Callable]] = {
    "yt": ("YouTube", "youtube.com", ("length", "date", "type", "sort"), _yt),
    "peertube": ("PeerTube", "sepiasearch.org", ("length", "date", "type", "sort"), _peertube),
    "archive": ("Internet Archive", "archive.org", ("date", "type", "sort"), _archive),
    "dailymotion": ("Dailymotion", "dailymotion.com", ("length", "date", "type", "sort"), _dailymotion),
    "odysee": ("Odysee", "odysee.com", ("length", "date", "type", "sort"), _odysee),
    "vimeo": ("Vimeo", "vimeo.com", ("type", "sort"), _vimeo),
}
#: The types each source has, beyond video.
TYPES_OF = {"yt": TYPES, "peertube": ("video", "live"), "archive": ("video",),
            "dailymotion": ("video", "live"), "odysee": ("video",),
            "vimeo": ("video",)}


def offered() -> list[str]:
    """The sources this install searches: YouTube only with the add-on,
    Vimeo only with MEDIA_VIMEO_TOKEN."""
    return [s for s in SOURCES if (s != "yt" or _yt_addon() is not None) and (s != "vimeo" or vimeo_token())]


def sources() -> list[dict]:
    """What GET /videos/sources lists (lists come with step 3)."""
    out = []
    for sid in offered():
        name, site, filters, _ = SOURCES[sid]
        out.append({"id": sid, "name": name, "site": site, "filters": list(filters),
                    "types": list(TYPES_OF[sid]), "lists": []})
    return out


def check(params: dict) -> dict:
    """The search's filters from a query's parameters; ValueError names a
    bad one."""
    f = {}
    for k, allowed in FILTERS.items():
        v = str(params.get(k) or "").strip().lower()
        if v == "relevance" and k == "sort":
            v = ""
        if v and v not in allowed:
            raise ValueError(f"{k} must be one of {', '.join(allowed)}")
        if v:
            f[k] = v
    return f


_CACHE: dict = {}
_LOCK = threading.Lock()


def clear_cache() -> None:
    with _LOCK:
        _CACHE.clear()
        _COOL.clear()


#: How long a source's answers are kept, where not CACHE_S: Vimeo allows a
#: token 50 asks a quarter hour (x-ratelimit-limit, 25 Nov 2026).
CACHE_S_OF = {"vimeo": 6 * 3600.0}
#: After a 429, a source is not asked until its Retry-After (or
#: x-ratelimit-reset) has passed, else this long; it is reported failed.
COOL_S = 900.0
_COOL: dict = {}


class Cooling(Exception):
    pass


def _cool_until(e: Any, now: float) -> float:
    """When a 429's source may be asked again: its Retry-After seconds, or
    its x-ratelimit-reset time (ISO or Unix), else COOL_S from now."""
    hdrs = getattr(e, "headers", None) or {}
    after = _int(hdrs.get("Retry-After"))
    if after is not None and 0 < after <= 3600:
        return now + after
    reset = str(hdrs.get("X-RateLimit-Reset") or hdrs.get("x-ratelimit-reset") or "")
    at = _int(reset) if reset.isdigit() else _iso(reset)
    if at and now < at <= now + 3600:
        return float(at)
    return now + COOL_S


def _ask(sid: str, q: str, f: dict, page: int, fetch: Fetch, now: float) -> dict:
    import urllib.error
    key = (sid, q.lower(), tuple(sorted(f.items())), page)
    with _LOCK:
        hit = _CACHE.get(key)
        cool = _COOL.get(sid, 0.0)
    if hit and now - hit[0] < CACHE_S_OF.get(sid, CACHE_S):
        return hit[1]
    if now < cool:
        mins = max(1, round((cool - now) / 60))
        raise Cooling(f"rate limited: asked again in {mins} min")
    fn = SOURCES[sid][3]
    try:
        rows, more, ignored = fn(q, f, page, fetch, LIMIT_S, now)
    except urllib.error.HTTPError as e:
        if e.code == 429:
            until = _cool_until(e, now)
            with _LOCK:
                _COOL[sid] = until
            raise Cooling(f"rate limited (429): asked again in {max(1, round((until - now) / 60))} min") from None
        raise
    got = {"rows": rows, "more": bool(more), "ignored": ignored}
    with _LOCK:
        if len(_CACHE) > 500:
            _CACHE.clear()
        _CACHE[key] = (now, got)
    return got


def _why(e: BaseException) -> str:
    import socket
    import urllib.error
    if isinstance(e, Cooling):
        return str(e)
    if isinstance(e, urllib.error.HTTPError):
        return f"answered {e.code}"
    if isinstance(e, (TimeoutError, socket.timeout)) or "timed out" in str(e):
        return "timed out"
    return str(e)[:200] or type(e).__name__


def search(q: str, wanted: Optional[list[str]] = None, filters: Optional[dict] = None, page: int = 1,
           fetch: Optional[Fetch] = None, clock: Callable[[], float] = time.time,
           limit_s: Optional[float] = None) -> dict:
    """What GET /videos/search answers (less `ok`). ValueError for a bad
    query; a source's own failure is in its `sources` entry."""
    q = (q or "").strip()
    if not q or len(q) > 200:
        raise ValueError("q is the words to search for, up to 200 characters")
    page = max(1, min(int(page or 1), MOST_PAGES))
    f = filters or {}
    have = offered()
    ids = [s for s in (wanted or have) if s in SOURCES]
    report: dict = {}
    for s in ids:
        if s not in have:
            report[s] = {"id": s, "ok": False, "error": "not offered here"}
    asked = [s for s in ids if s not in report]
    now = clock()
    fetch = fetch or _fetch_json
    pool = ThreadPoolExecutor(max_workers=max(1, len(asked)), thread_name_prefix="videos")
    futs = {s: pool.submit(_ask, s, q, f, page, fetch, now) for s in asked}
    wait(list(futs.values()), timeout=LIMIT_S if limit_s is None else limit_s)
    pool.shutdown(wait=False)
    lists: dict = {}
    for s, fut in futs.items():
        if not fut.done():
            fut.cancel()
            report[s] = {"id": s, "ok": False, "error": "timed out"}
            continue
        try:
            got = fut.result()
        except Exception as e:  # noqa: BLE001 — a failed source is reported, the rest stand
            _failures.swallowed(e)
            report[s] = {"id": s, "ok": False, "error": _why(e)}
            continue
        lists[s] = got["rows"]
        entry: dict = {"id": s, "ok": True, "count": len(got["rows"])}
        if got["more"]:
            entry["next"] = page + 1
        dropped = [k for k in f if k not in SOURCES[s][2]] + [k for k in got["ignored"] if k in f]
        if dropped:
            entry["ignored"] = sorted(set(dropped))
        report[s] = entry
    items: list = []
    longest = max((len(v) for v in lists.values()), default=0)
    for i in range(longest):
        for s in ids:
            rows = lists.get(s) or []
            if i < len(rows):
                items.append(rows[i])
    return {"q": q, "page": page, "filters": f, "items": items, "sources": [report[s] for s in ids]}
