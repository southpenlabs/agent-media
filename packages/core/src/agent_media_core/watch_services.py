"""What is on a streaming service: Discover's "On a service" list
(server-contract.md §6.54 "By service"; David, 13 Nov 2026: "add tubi
too" — browse what a service has, or what is free to watch).

Trakt has no such list; TMDb's discover does (its provider data is
JustWatch's, credited in `attribution`):

  services(kind)   the region's services, most used first: GET
                   /watch/providers/movie|tv?watch_region=<region>, in
                   the region's display order, without the channels sold
                   inside another service and JustWatch's own
  discover(...)    a service's movies or shows, most popular first: GET
                   /discover/movie|tv?watch_region=&with_watch_providers=
                   &with_watch_monetization_types=…; `free` is everything
                   free or with ads, whoever has it

The rows are §6.54's items made from TMDb's own answer: a TMDb id and
poster, no Trakt id (none is asked for per row: a title's page resolves it
when the row is opened, `GET /watch/title?id=tmdb:<id>`, and a write takes
the TMDb id as it is). `watched` and `on_watchlist` are told by TMDb id
from the sets trakt.py already keeps.

Everything is read through tmdb._request (the seam the tests stub) and
kept an hour (the genre list a day). Only when TMDb is set up.
"""

from __future__ import annotations

import re
import threading
import time
from typing import Optional

from . import tmdb
from . import trakt as T

TTL_S = 3600.0
GENRES_TTL_S = 86400.0
#: How many services the answer offers.
SERVICES_MAX = 25
#: TMDb's own page size, and its last page.
PAGE_SIZE = 20
PAGE_MAX = 500
FREE = "free"
FREE_NAME = "Free to watch"
#: Left out of the offer: a channel sold inside another service (found by
#: that service), JustWatch's own, and a second tier of one already listed.
_SKIP = re.compile(r"\b(amazon|apple tv|roku premium) channel$|^justwatch\b|\bwith ads$", re.I)
#: TMDb's genre words that Trakt spells otherwise (a slug made from the name).
_ALIAS = {"sci-fi": "science-fiction", "kids": "children", "talk": "talk-show"}
#: Discover's filters TMDb can answer too (the rest are ignored, and said so).
FILTERS = ("rating", "runtime", "language", "country")

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


def _what(kind: str) -> str:
    if kind not in T.KINDS:
        raise ValueError("kind must be movie or tv")
    return kind


def _all_services(kind: str, now: float) -> list[dict]:
    """Every provider TMDb lists for the region, in its display order."""
    reg = tmdb.region()

    def get():
        rows = tmdb._request(f"/watch/providers/{kind}", {"watch_region": reg}).get("results")
        out = []
        for p in rows if isinstance(rows, list) else []:
            if not isinstance(p, dict) or p.get("provider_id") is None or not p.get("provider_name"):
                continue
            order = (p.get("display_priorities") or {}).get(reg, p.get("display_priority"))
            out.append((order if isinstance(order, (int, float)) else 10 ** 6,
                        {"id": str(p["provider_id"]), "name": str(p["provider_name"]),
                         "logo": tmdb.poster_url(str(p.get("logo_path") or ""), tmdb.LOGO_SIZE)}))
        out.sort(key=lambda x: x[0])
        return [row for _, row in out]
    return _cached(f"s:{kind}:{reg}", now, get)


def services(kind: str, now: Optional[float] = None) -> list[dict]:
    """`[{id, name, logo}]`: the region's services the app offers, most
    used first, at most SERVICES_MAX."""
    now = time.time() if now is None else now
    return [s for s in _all_services(_what(kind), now) if not _SKIP.search(s["name"])][:SERVICES_MAX]


def _genres(kind: str, now: float) -> tuple[dict, dict]:
    """(Trakt genre slug → TMDb genre id, TMDb genre id → name), from
    TMDb's own genre list. A double name answers for both its halves
    ("Sci-Fi & Fantasy": science-fiction and fantasy)."""
    def get():
        rows = tmdb._request(f"/genre/{kind}/list").get("genres")
        by_slug: dict = {}
        names: dict = {}
        for g in rows if isinstance(rows, list) else []:
            if not isinstance(g, dict) or not isinstance(g.get("id"), int) or not g.get("name"):
                continue
            names[g["id"]] = str(g["name"])
            for part in re.split(r"\s*&\s*", str(g["name"])):
                slug = re.sub(r"[^a-z0-9]+", "-", part.lower()).strip("-")
                by_slug.setdefault(_ALIAS.get(slug, slug), g["id"])
        return by_slug, names
    return _cached(f"g:{kind}", now, get, GENRES_TTL_S)


def _filter_params(kind: str, filters: Optional[dict]) -> tuple[dict, list[str]]:
    """TMDb's parameters for the filters it can answer, and the names of
    those it cannot. ValueError for a value Discover does not offer."""
    T._filter_query(kind, filters)
    out: dict = {}
    ignored: list[str] = []
    for k, v in (filters or {}).items():
        v = str(v or "").strip()
        if not v:
            continue
        lo, _, hi = v.partition("-")
        if k == "rating":
            out["vote_average.gte"] = int(lo) / 10
            if int(hi) < 100:
                out["vote_average.lte"] = int(hi) / 10
        elif k == "runtime":
            out["with_runtime.gte"], out["with_runtime.lte"] = int(lo), int(hi)
        elif k == "language":
            out["with_original_language"] = v
        elif k == "country":
            out["with_origin_country"] = v.upper()
        else:
            ignored.append(k)
    return out, ignored


def _item(kind: str, o: dict, names: dict) -> Optional[dict]:
    """A §6.54 item from one of TMDb's discover rows; None without an id or a title."""
    tid = o.get("id")
    title = str(o.get("title" if kind == "movie" else "name") or "").strip()
    if not isinstance(tid, int) or not title:
        return None
    date = str(o.get("release_date" if kind == "movie" else "first_air_date") or "")
    one = "movie" if kind == "movie" else "show"
    r, votes = o.get("vote_average"), o.get("vote_count")
    scores = ({"rating": round(float(r), 1), "votes": int(votes or 0)}
              if isinstance(r, (int, float)) and r > 0 else {})
    item = {"id": f"{one}:tmdb:{tid}", "kind": one, "title": title,
            "year": int(date[:4]) if date[:4].isdigit() else None, "runtime": None,
            "genres": [names[g] for g in o.get("genre_ids") or [] if g in names], **scores,
            "overview": T._short(o.get("overview")),
            "url": f"https://www.themoviedb.org/{kind}/{tid}", "ids": {"tmdb": tid}}
    poster = tmdb.poster_url(str(o.get("poster_path") or ""))
    if poster:
        item["poster"] = poster
    return item


def _mark(kind: str, items: list[dict], now: float) -> None:
    """`watched` and `on_watchlist` on each, by TMDb id."""
    try:
        seen = T._watched_ids(kind, now, by="tmdb")
    except (T.TraktError, ValueError):
        seen = set()
    try:
        listed = {(i.get("ids") or {}).get("tmdb") for i in T._watchlist(kind, now)}
    except (T.TraktError, ValueError):
        listed = set()
    for i in items:
        i["watched"] = i["ids"]["tmdb"] in seen
        i["on_watchlist"] = i["ids"]["tmdb"] in listed


def discover(kind: str, service: str = "", genre: str = "", year: str = "", page: int = 1,
             hide_watched: bool = True, now: Optional[float] = None, filters: Optional[dict] = None,
             lists: Optional[list] = None) -> dict:
    """What GET /watch/discover?list=service answers (less `ok`). Without
    `service`: the services to choose from and no items. ValueError when
    TMDb is not set up, or for a bad kind, service, genre, year or filter;
    other errors from TMDb pass through."""
    _what(kind)
    if not tmdb.set_up():
        raise ValueError("no TMDb credential on this server (MEDIA_TMDB_TOKEN or MEDIA_TMDB_API_KEY)")
    now = time.time() if now is None else now
    reg = tmdb.region()
    service = str(service or "").strip().lower()
    if service and service != FREE and not (service.isdigit() and len(service) <= 9):
        raise ValueError("service is one of the services' ids, or free")
    genre = str(genre or "").strip().lower()
    if genre and not all(c.isalnum() or c == "-" for c in genre):
        raise ValueError("genre is one of the genres' slugs")
    span = T._years(year)
    params, ignored = _filter_params(kind, filters)
    page = max(1, min(int(page or 1), PAGE_MAX))
    me = T.signed_in()
    by_slug, names = _genres(kind, now)
    offered = services(kind, now)
    chosen = None
    if service == FREE:
        chosen = {"id": FREE, "name": FREE_NAME}
    elif service:
        chosen = next((s for s in _all_services(kind, now) if s["id"] == service), None)
        if chosen is None:
            raise ValueError(f"service is not one TMDb lists for {reg}")
    items: list[dict] = []
    more = False
    if chosen:
        q = {"watch_region": reg, "sort_by": "popularity.desc", "include_adult": "false", "page": page,
             "with_watch_monetization_types": "free|ads" if service == FREE else "flatrate|free|ads|rent|buy",
             **params}
        if service != FREE:
            q["with_watch_providers"] = service
        if genre:
            if genre in by_slug:
                q["with_genres"] = by_slug[genre]
            else:
                ignored.append("genre")
        if span:
            field = "primary_release_date" if kind == "movie" else "first_air_date"
            q[f"{field}.gte"], q[f"{field}.lte"] = f"{span[0]}-01-01", f"{span[1]}-12-31"
        key = "d:" + kind + ":" + "&".join(f"{k}={v}" for k, v in sorted(q.items()))
        got = _cached(key, now, lambda: tmdb._request(f"/discover/{kind}", q))
        rows = got.get("results") if isinstance(got, dict) else None
        total = got.get("total_pages") if isinstance(got, dict) else None
        items = [i for i in (_item(kind, o, names) for o in rows or [] if isinstance(o, dict)) if i]
        more = isinstance(total, int) and page < min(total, PAGE_MAX)
        if me:
            _mark(kind, items, now)
            if hide_watched:
                items = [i for i in items if not i["watched"]]
    try:
        gs = [g for g in T.genres(kind, now) if g["slug"] in by_slug]
    except T.TraktError:
        gs = []
    return {"kind": kind, "list": "service", "lists": list(lists or ["service"]), "page": page, "more": more,
            "hide_watched": hide_watched and me, "signed_in": me, "items": items, "genres": gs,
            "filters": [f for f in T.filters_for(kind) if f["id"] in FILTERS],
            "service": chosen, "services": offered, "region": reg, "ignored": ignored,
            "attribution": tmdb.ATTRIBUTION}
