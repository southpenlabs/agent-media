"""Movies and Shows in depth, from Trakt (server-contract.md §6.54 "Pages,
people, lists"; David, 12 Nov 2026: "Can we have full Discovery and search
for movies and shows too?"): what Music has from MusicBrainz and Podcasts
from a show's feed.

  search_all(q)      movies, shows, people and lists by words
  title(kind, id)    a movie's or show's page: facts, cast and crew,
                     related; its trailer (TMDb's, Trakt's, or one found
                     by a YouTube search) and its ratings (IMDb, Rotten
                     Tomatoes, Metacritic … as Trakt carries them); a
                     show's seasons (how far watched) and the next episode
                     to air
  season(id, n)      a season's episodes, each watched or not
  person(id)         a person: bio line, photo (TMDb, when set), and what
                     they made — movies and shows, acting and crew, by year
  lists(which)       Trakt's trending and popular lists, and this account's
  list_items(id)     a list's movies and shows
  calendar(kind)     what airs soon for the shows watched; the watchlist's
                     movies coming out

Everything is read with trakt.py's token, HTTP seam (`trakt._request`) and
cache: what is the same for everyone under a public key (`d:…`: pages and
people a day, lists an hour, kept through a write), what is this account's
a quarter of an hour (cleared by a write). TMDb only adds (posters, a
person's photo); without it everything here still answers.
"""

from __future__ import annotations

import time
import urllib.parse
from typing import Optional

from . import trakt as T

PAGE_TTL_S = 86400.0
LISTS_TTL_S = 3600.0
PERSONAL_TTL_S = 900.0
CAST_MAX = 30
CREW_MAX = 30
RELATED_MAX = 12
#: Crew departments in the order shown, with the jobs that matter first.
DEPARTMENTS = ("directing", "writing", "created by", "production", "sound", "camera", "editing", "art",
               "costume & make-up", "visual effects", "crew")


def _one(kind: str) -> str:
    if kind not in T.KINDS:
        raise ValueError("kind must be movie or tv")
    return "movies" if kind == "movie" else "shows"


def _id(x) -> str:
    x = str(x or "").strip().lower()
    if not x or len(x) > 120 or not all(c.isalnum() or c in "-_" for c in x):
        raise ValueError("id is a Trakt id or slug")
    return x


def _now(now: Optional[float]) -> float:
    return time.time() if now is None else now


def _pub(key: str, path: str, now: float, ttl: float = PAGE_TTL_S):
    return T._cached(f"d:{key}", now, lambda: T._public_get(path), ttl)


def _mine(key: str, path: str, now: float, ttl: float = PERSONAL_TTL_S):
    return T._cached(f"my:{key}", now, lambda: T._get(path, now), ttl)


def _mark(kind: str, items: list[dict], now: float) -> None:
    """`watched` and `on_watchlist` on each movie or show, when signed in."""
    if not T.signed_in() or not items:
        return
    try:
        seen = T._watched_ids(kind, now)
    except (T.TraktError, ValueError):
        seen = set()
    for i in items:
        i["watched"] = i["ids"].get("trakt") in seen
    T._mark_watchlist(kind, items, now)


def _posters(items: list[dict]) -> None:
    from . import tmdb
    if tmdb.set_up() and items:
        try:
            tmdb.add_posters(items)
        except Exception:  # noqa: BLE001 — the list without its pictures
            pass


def _item(kind: str, o: dict) -> dict:
    return T._movie(o) if kind == "movie" else T._show(o)


def _person_row(p: dict) -> dict:
    ids = p.get("ids") or {}
    return {"id": ids.get("slug") or str(ids.get("trakt") or ""), "name": str(p.get("name") or ""),
            "ids": {k: ids[k] for k in ("trakt", "slug", "tmdb", "imdb") if ids.get(k) is not None},
            "known_for": str(p.get("known_for_department") or "").capitalize() or None,
            "url": f"{T.SITE}/people/{ids.get('slug') or ids.get('trakt')}"}


def _list_row(lst: dict, extra: Optional[dict] = None) -> dict:
    ids = lst.get("ids") or {}
    user = lst.get("user") or {}
    return {"id": str(ids.get("trakt") or ids.get("slug") or ""), "name": str(lst.get("name") or ""),
            "description": T._short(lst.get("description")) or None, "item_count": lst.get("item_count"),
            "likes": lst.get("likes"), "user": user.get("name") or user.get("username") or None,
            "url": f"{T.SITE}/lists/{ids.get('trakt')}" if ids.get("trakt") else None, **(extra or {})}


# ---- search ---------------------------------------------------------------------------------

def search_all(q: str, now: Optional[float] = None) -> dict:
    """`{movies, shows, people, lists}` for the words."""
    q = " ".join(str(q or "").split())[:120]
    if len(q) < 2:
        raise ValueError("type at least two letters")
    if not T.set_up():
        raise ValueError(T.NOT_SET_UP)
    now = _now(now)
    rows = _pub(f"sa:{q.lower()}", f"/search/movie,show,person,list?query={urllib.parse.quote(q)}&extended=full&limit=40",
                now, LISTS_TTL_S)
    out: dict = {"movies": [], "shows": [], "people": [], "lists": []}
    for r in rows if isinstance(rows, list) else []:
        t = r.get("type") if isinstance(r, dict) else None
        o = r.get(t) if t else None
        if not isinstance(o, dict):
            continue
        if t == "movie":
            out["movies"].append(T._movie(o))
        elif t == "show":
            out["shows"].append(T._show(o))
        elif t == "person":
            out["people"].append(_person_row(o))
        elif t == "list" and (o.get("privacy") or "public") == "public":
            out["lists"].append(_list_row(o))
    _mark("movie", out["movies"], now)
    _mark("tv", out["shows"], now)
    _posters(out["movies"] + out["shows"])
    _photos(out["people"][:8])
    return {"q": q, **{k: v[:10] for k, v in out.items()}}


def people(q: str, now: Optional[float] = None) -> list[dict]:
    """People by name (the Media page's one search)."""
    return search_all(q, now)["people"]


# ---- a title's page -------------------------------------------------------------------------

def _credits(got) -> tuple[list[dict], list[dict]]:
    cast, crew = [], []
    if not isinstance(got, dict):
        return cast, crew
    for c in got.get("cast") or []:
        p = c.get("person") if isinstance(c, dict) else None
        if isinstance(p, dict) and p.get("name"):
            chars = c.get("characters") or ([c["character"]] if c.get("character") else [])
            cast.append({**_person_row(p), "character": ", ".join(str(x) for x in chars if x) or None,
                         "episodes": c.get("episode_count")})
    by = got.get("crew") or {}
    for dept in sorted(by, key=lambda d: DEPARTMENTS.index(d) if d in DEPARTMENTS else 99):
        for c in by.get(dept) or []:
            p = c.get("person") if isinstance(c, dict) else None
            if isinstance(p, dict) and p.get("name"):
                jobs = c.get("jobs") or ([c["job"]] if c.get("job") else [])
                crew.append({**_person_row(p), "job": ", ".join(str(x) for x in jobs if x) or None,
                             "department": dept.capitalize()})
    # One row a person in the crew: their jobs together.
    merged: dict[str, dict] = {}
    for c in crew:
        m = merged.get(c["id"])
        if m:
            if c["job"] and c["job"] not in (m["job"] or ""):
                m["job"] = ", ".join(x for x in (m["job"], c["job"]) if x)
        else:
            merged[c["id"]] = c
    return cast[:CAST_MAX], list(merged.values())[:CREW_MAX]


#: The ratings a title's page shows, in the order shown: (source, Trakt's key
#: in /ratings?extended=all, its field, label, how the number is written).
#: "pct": a whole percentage; "pct10": 0–10 written as a percentage (Trakt's
#: own way); "one": one decimal place; "whole": a whole number. `out_of` is
#: what the number is out of, when it is not a percentage.
RATING_SOURCES = (
    ("imdb", "imdb", "rating", "IMDb", "one", "10"),
    ("rotten_tomatoes", "rotten_tomatoes", "rating", "Rotten Tomatoes", "pct", None),
    ("rotten_tomatoes_audience", "rotten_tomatoes", "user_rating", "RT audience", "pct", None),
    ("metascore", "metascore", "rating", "Metacritic", "whole", "100"),
    ("trakt", "trakt", "rating", "Trakt", "pct10", None),
    ("tmdb", "tmdb", "rating", "TMDB", "one", "10"),
    ("letterboxd", "letterboxd", "rating", "Letterboxd", "one", "5"),
    ("mal", "mal", "rating", "MyAnimeList", "one", "10"),
)


def _ratings(got, own: Optional[dict] = None) -> list[dict]:
    """A title's ratings as the app draws them, from Trakt's
    `/ratings?extended=all`: `[{source, label, value, out_of?, votes?,
    state?, link?}]` in RATING_SOURCES' order, only the sources with a
    number. `own` (the title itself) gives Trakt's own when the answer has
    none."""
    got = dict(got) if isinstance(got, dict) else {}
    if not isinstance(got.get("trakt"), dict) and isinstance(got.get("rating"), (int, float)):
        got["trakt"] = {"rating": got.get("rating"), "votes": got.get("votes")}  # the answer without extended=all
    if not isinstance(got.get("trakt"), dict) and own:
        got["trakt"] = {"rating": own.get("rating"), "votes": own.get("votes")}
    out = []
    for source, key, field, label, how, out_of in RATING_SOURCES:
        o = got.get(key)
        r = o.get(field) if isinstance(o, dict) else None
        if isinstance(r, bool) or not isinstance(r, (int, float)):
            continue
        state = o.get("user_state" if field == "user_rating" else "state")
        votes = o.get("votes") if field == "rating" else None
        votes = int(votes) if isinstance(votes, (int, float)) and not isinstance(votes, bool) and votes > 0 else None
        if r <= 0 and not votes and not state:
            continue  # 0 with nobody voting: not rated
        value = (f"{round(r)}%" if how == "pct" else f"{round(r * 10)}%" if how == "pct10"
                 else str(round(r)) if how == "whole" else f"{r:.1f}")
        row = {"source": source, "label": label, "value": value}
        if out_of:
            row["out_of"] = out_of
        if votes:
            row["votes"] = votes
        if isinstance(state, str) and state:
            row["state"] = state
        link = o.get("link")
        if isinstance(link, str) and link.startswith("https://"):
            row["link"] = link
        out.append(row)
    return out


def _tmdb_trailer(kind: str, item: dict) -> Optional[str]:
    """TMDb's trailer for the title, when TMDb is set up and has one."""
    from . import tmdb
    tmdb_id = (item.get("ids") or {}).get("tmdb")
    if not tmdb.set_up() or tmdb_id is None:
        return None
    try:
        return tmdb.details(kind, int(tmdb_id)).get("trailer") or None
    except Exception:  # noqa: BLE001 — Trakt's own then
        return None


#: A trailer found by searching is kept a week; "none found" a day.
TRAILER_FOUND_TTL_S = 7 * 86400.0
TRAILER_NONE_TTL_S = 86400.0
TRAILER_KEEP = 500


def _youtube_search(q: str) -> list[dict]:
    """`[{id, title}]` for the words, from whichever YouTube search the host
    already has (music_versions' two: Mopidy's "youtube-search" add-on, else
    the "youtube-radio" add-on's yt-dlp search); [] with neither. The seam
    the tests stub."""
    from . import music_bed_favourites as fav
    from . import youtube_addon

    if fav._may_search():
        return [{"id": r.get("id"), "title": r.get("title") or ""} for r in fav.search(q)]
    if youtube_addon.get("youtube-radio") is not None:
        from . import radio_dj
        return [{"id": r.get("id"), "title": r.get("title") or ""} for r in radio_dj.search([q])]
    return []


def _trailers_path():
    from ._paths import state_dir
    return state_dir() / "watch-trailers.json"


def _searched_trailer(kind: str, item: dict, now: float) -> Optional[str]:
    """A trailer by search, for a title neither TMDb nor Trakt has one for:
    "<title> <year> official trailer" asked of the host's YouTube search, the
    first result with "trailer" in its title that names the title, else the
    first that names it. Kept by title (a week; a day when nothing was
    found), so only a title page's first opening searches."""
    import json
    import os
    import re

    name = str(item.get("title") or "").strip()
    key = f"{kind}:{(item.get('ids') or {}).get('trakt') or item.get('id')}"
    if not name:
        return None
    path = _trailers_path()
    try:
        kept = json.loads(path.read_text())
        kept = kept if isinstance(kept, dict) else {}
    except (OSError, ValueError):
        kept = {}
    hit = kept.get(key)
    if isinstance(hit, dict):
        ttl = TRAILER_FOUND_TTL_S if hit.get("url") else TRAILER_NONE_TTL_S
        if now - float(hit.get("at") or 0) < ttl:
            return hit.get("url") or None
    try:
        rows = _youtube_search(" ".join(x for x in (name, str(item.get("year") or ""), "official trailer") if x))
    except Exception:  # noqa: BLE001 — asked again next time
        return None

    def fold(x: str) -> str:
        return re.sub(r"[^a-z0-9]+", " ", str(x).lower()).strip()

    want = fold(name)
    rows = [r for r in rows if isinstance(r, dict) and re.fullmatch(r"[A-Za-z0-9_-]{11}", str(r.get("id") or ""))
            and want and want in fold(r.get("title") or "")]
    best = next((r for r in rows if "trailer" in fold(r["title"])), rows[0] if rows else None)
    url = f"https://www.youtube.com/watch?v={best['id']}" if best else None
    kept[key] = {"url": url, "at": now}
    if len(kept) > TRAILER_KEEP:
        kept = dict(sorted(kept.items(), key=lambda kv: -float(kv[1].get("at") or 0))[:TRAILER_KEEP])
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f".{path.name}.{os.getpid()}")
        tmp.write_text(json.dumps(kept))
        os.replace(tmp, path)
    except OSError:
        pass
    return url


def _trailer(kind: str, item: dict, now: float) -> tuple[Optional[str], Optional[str]]:
    """(address, source) of a title's trailer: TMDb's when it is set up and
    has one, else Trakt's own, else one found by search; (None, None)."""
    got = _tmdb_trailer(kind, item)
    if got:
        return got, "tmdb"
    if item.get("trailer"):
        return item["trailer"], "trakt"
    got = _searched_trailer(kind, item, now)
    return (got, "youtube_search") if got else (None, None)


def title(kind: str, ident, now: Optional[float] = None) -> dict:
    """A movie's or a show's page (what GET /watch/title answers, less `ok`)."""
    what, tid, now = _one(kind), _id(ident), _now(now)
    if not T.set_up():
        raise ValueError(T.NOT_SET_UP)
    o = _pub(f"t:{what}:{tid}", f"/{what}/{tid}?extended=full", now)
    if not isinstance(o, dict) or not o.get("ids"):
        raise LookupError("Trakt has no such title")
    item = _item(kind, o)
    item["overview"] = " ".join(str(o.get("overview") or "").split()) or None
    item.update({"tagline": o.get("tagline") or None, "certification": o.get("certification") or None,
                 "language": o.get("language") or None, "country": o.get("country") or None,
                 "status": o.get("status") or None, "homepage": o.get("homepage") or None,
                 "trailer": o.get("trailer") or None,
                 "released": o.get("released") or o.get("first_aired") or None})
    if kind == "tv":
        item.update({"network": o.get("network") or None, "aired_episodes": o.get("aired_episodes")})
    out: dict = {"kind": kind, "title": item, "trailer": None, "trailer_source": None, "ratings": [], "cast": [], "crew": [], "related": []}
    try:
        got = _pub(f"tg:{what}:{tid}", f"/{what}/{tid}/ratings?extended=all", now)
    except T.TraktError:
        got = None
    out["ratings"] = _ratings(got, o)
    out["trailer"], out["trailer_source"] = _trailer(kind, item, now)
    try:
        out["cast"], out["crew"] = _credits(_pub(f"tp:{what}:{tid}", f"/{what}/{tid}/people", now))
    except T.TraktError:
        pass
    try:
        rel = _pub(f"tr:{what}:{tid}", f"/{what}/{tid}/related?extended=full&limit={RELATED_MAX}", now)
        out["related"] = [_item(kind, r) for r in rel if isinstance(r, dict) and r.get("ids")] if isinstance(rel, list) else []
    except T.TraktError:
        pass
    _mark(kind, [item] + out["related"], now)
    _posters([item] + out["related"])
    _photos(out["cast"][:12])
    if kind == "tv":
        out["seasons"] = _seasons(tid, item["ids"].get("trakt"), now)
        try:
            code, nxt = T._request("GET", f"/shows/{tid}/next_episode?extended=full")
            if code == 200 and isinstance(nxt, dict):
                out["next_episode"] = {"season": nxt.get("season"), "number": nxt.get("number"),
                                       "title": nxt.get("title") or None, "first_aired": nxt.get("first_aired")}
        except T.TraktError:
            pass
    return out


def _progress(trakt_id, now: float) -> dict:
    """{season number: {episode number: watched}} for this account, or {}."""
    if not T.signed_in() or trakt_id is None:
        return {}
    try:
        prog = T._cached(f"p:{trakt_id}", now, lambda: T._get(
            f"/shows/{trakt_id}/progress/watched?hidden=false&specials=false&count_specials=false&extended=full", now))
    except (T.TraktError, ValueError):
        return {}
    out: dict = {}
    for s in (prog.get("seasons") if isinstance(prog, dict) else None) or []:
        if isinstance(s, dict) and isinstance(s.get("number"), int):
            out[s["number"]] = {e.get("number"): bool(e.get("completed")) for e in s.get("episodes") or [] if isinstance(e, dict)}
    return out


def _seasons(tid: str, trakt_id, now: float) -> list[dict]:
    try:
        rows = _pub(f"ts:{tid}", f"/shows/{tid}/seasons?extended=full", now)
    except T.TraktError:
        return []
    prog = _progress(trakt_id, now)
    out = []
    for s in rows if isinstance(rows, list) else []:
        if not isinstance(s, dict) or not isinstance(s.get("number"), int) or s["number"] < 1:
            continue
        seen = prog.get(s["number"]) or {}
        aired = int(s.get("aired_episodes") or 0)
        out.append({"number": s["number"], "title": s.get("title") or None,
                    "episodes": int(s.get("episode_count") or 0), "aired": aired,
                    "completed": sum(1 for v in seen.values() if v),
                    "first_aired": s.get("first_aired") or None})
    return out


def season(ident, number, now: Optional[float] = None) -> dict:
    """A season's episodes, each `watched` when signed in."""
    tid, now = _id(ident), _now(now)
    if not str(number).lstrip("-").isdigit() or not 0 <= int(number) <= 500:
        raise ValueError("season is its number")
    n = int(number)
    rows = _pub(f"te:{tid}:{n}", f"/shows/{tid}/seasons/{n}?extended=full", now)
    show = _pub(f"t:shows:{tid}", f"/shows/{tid}?extended=full", now)
    trakt_id = ((show or {}).get("ids") or {}).get("trakt") if isinstance(show, dict) else None
    seen = (_progress(trakt_id, now).get(n)) or {}
    eps = []
    for e in rows if isinstance(rows, list) else []:
        if not isinstance(e, dict) or e.get("number") is None:
            continue
        ids = e.get("ids") or {}
        eps.append({"season": n, "number": e["number"], "title": e.get("title") or None,
                    "overview": T._short(e.get("overview")) or None, "first_aired": e.get("first_aired") or None,
                    "runtime": e.get("runtime") or None, **T._scores(e),
                    "ids": {k: ids[k] for k in ("trakt", "tmdb", "imdb") if ids.get(k) is not None},
                    **({"watched": bool(seen.get(e["number"]))} if T.signed_in() else {})})
    return {"season": n, "episodes": eps}


# ---- people ---------------------------------------------------------------------------------

def _photos(rows: list[dict]) -> None:
    from . import tmdb
    if not tmdb.set_up():
        return
    try:
        tmdb.add_photos(rows)
    except Exception:  # noqa: BLE001 — the people without their pictures
        pass


def _filmography(kind: str, got, now: float) -> dict:
    one = "movie" if kind == "movie" else "show"
    cast, crew = [], {}
    if isinstance(got, dict):
        for c in got.get("cast") or []:
            o = c.get(one) if isinstance(c, dict) else None
            if isinstance(o, dict) and o.get("ids"):
                chars = c.get("characters") or ([c["character"]] if c.get("character") else [])
                cast.append({**_item(kind, o), "role": ", ".join(str(x) for x in chars if x) or None,
                             **({"episodes": c["episode_count"]} if c.get("episode_count") else {})})
        for dept, rows in (got.get("crew") or {}).items():
            for c in rows or []:
                o = c.get(one) if isinstance(c, dict) else None
                if isinstance(o, dict) and o.get("ids"):
                    jobs = c.get("jobs") or ([c["job"]] if c.get("job") else [])
                    key = o["ids"].get("trakt")
                    if key in crew:
                        crew[key]["role"] = ", ".join(x for x in (crew[key]["role"], ", ".join(jobs)) if x)
                    else:
                        crew[key] = {**_item(kind, o), "role": ", ".join(str(x) for x in jobs if x) or None,
                                     "department": dept.capitalize()}
    crew_rows = list(crew.values())
    for rows in (cast, crew_rows):
        rows.sort(key=lambda i: -(i.get("year") or 0))
    _mark(kind, cast + crew_rows, now)
    return {"cast": cast, "crew": crew_rows}


def person(ident, now: Optional[float] = None) -> dict:
    """A person's page (what GET /watch/person answers, less `ok`)."""
    pid, now = _id(ident), _now(now)
    if not T.set_up():
        raise ValueError(T.NOT_SET_UP)
    p = _pub(f"pp:{pid}", f"/people/{pid}?extended=full", now)
    if not isinstance(p, dict) or not p.get("name"):
        raise LookupError("Trakt has no such person")
    row = _person_row(p)
    row.update({"biography": " ".join(str(p.get("biography") or "").split())[:3000] or None,
                "birthday": p.get("birthday") or None, "death": p.get("death") or None,
                "birthplace": p.get("birthplace") or None, "homepage": p.get("homepage") or None})
    _photos([row])
    movies = _filmography("movie", _pub(f"pm:{pid}", f"/people/{pid}/movies?extended=full", now), now)
    shows = _filmography("tv", _pub(f"ps:{pid}", f"/people/{pid}/shows?extended=full", now), now)
    _posters((movies["cast"] + shows["cast"] + movies["crew"] + shows["crew"])[:60])
    best = sorted(movies["cast"] + shows["cast"] + movies["crew"] + shows["crew"], key=lambda i: -(i.get("votes") or 0))
    seen, known = set(), []
    for i in best:
        if i["id"] not in seen:
            seen.add(i["id"])
            known.append(i["title"])
        if len(known) >= 4:
            break
    row["known_titles"] = known
    return {"person": row, "movies": movies, "shows": shows}


# ---- lists ----------------------------------------------------------------------------------

LIST_KINDS = ("trending", "popular", "mine")


def lists(which: str, page: int = 1, now: Optional[float] = None) -> dict:
    if which not in LIST_KINDS:
        raise ValueError(f"which must be one of {', '.join(LIST_KINDS)}")
    now, page = _now(now), max(1, min(int(page or 1), 20))
    me = T.signed_in()
    if which == "mine":
        if not me:
            raise ValueError("your lists need Trakt connected: connect it in Settings")
        rows = _mine("lists", "/users/me/lists", now)
        out = [_list_row(r, {"mine": True}) for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []
        return {"which": which, "kinds": list(LIST_KINDS), "page": 1, "more": False, "lists": out}
    rows = _pub(f"l:{which}:{page}", f"/lists/{which}?page={page}&limit=20", now, LISTS_TTL_S)
    out = []
    for r in rows if isinstance(rows, list) else []:
        lst = r.get("list") if isinstance(r, dict) else None
        if isinstance(lst, dict):
            out.append(_list_row(lst, {"likes": r.get("like_count") or lst.get("likes")}))
    return {"which": which, "kinds": [k for k in LIST_KINDS if k != "mine" or me], "page": page,
            "more": len(out) >= 20, "lists": out}


def list_items(ident, mine: bool = False, page: int = 1, now: Optional[float] = None) -> dict:
    """A list's movies and shows, in its order, 20 a page."""
    lid, now, page = _id(ident), _now(now), max(1, min(int(page or 1), 100))
    tail = f"items/movie,show?extended=full&page={page}&limit=20"
    if mine:
        if not T.signed_in():
            raise ValueError("your lists need Trakt connected: connect it in Settings")
        rows = _mine(f"li:{lid}:{page}", f"/users/me/lists/{lid}/{tail}", now)
    else:
        rows = _pub(f"li:{lid}:{page}", f"/lists/{lid}/{tail}", now, LISTS_TTL_S)
    movies, shows, items = [], [], []
    for r in rows if isinstance(rows, list) else []:
        t = r.get("type") if isinstance(r, dict) else None
        o = r.get(t) if t in ("movie", "show") else None
        if isinstance(o, dict) and o.get("ids"):
            it = T._movie(o) if t == "movie" else T._show(o)
            (movies if t == "movie" else shows).append(it)
            items.append(it)
    _mark("movie", movies, now)
    _mark("tv", shows, now)
    _posters(items)
    return {"id": lid, "page": page, "more": len(rows) >= 20 if isinstance(rows, list) else False, "items": items}


# ---- what is coming -------------------------------------------------------------------------

def calendar(kind: str, days: int = 14, now: Optional[float] = None) -> dict:
    """Airing soon for the shows watched (kind tv), or the watchlist's
    movies coming out (kind movie), from today, soonest first."""
    _one(kind)
    if not T.signed_in():
        raise ValueError("the calendar needs Trakt connected: connect it in Settings")
    now = _now(now)
    days = max(1, min(int(days or 14), 31))
    start = time.strftime("%Y-%m-%d", time.gmtime(now))
    what = "shows" if kind == "tv" else "movies"
    rows = _mine(f"cal:{what}:{start}:{days}", f"/calendars/my/{what}/{start}/{days}?extended=full", now)
    items = []
    for r in rows if isinstance(rows, list) else []:
        if not isinstance(r, dict):
            continue
        if kind == "tv" and isinstance(r.get("episode"), dict):
            it = T._episode(r["episode"], r.get("show") or {})
            it["first_aired"] = r.get("first_aired")
            items.append(it)
        elif kind == "movie" and isinstance(r.get("movie"), dict):
            it = T._movie(r["movie"])
            it["released"] = r.get("released")
            items.append(it)
    items.sort(key=lambda i: str(i.get("first_aired") or i.get("released") or ""))
    _posters(items)
    return {"kind": kind, "from": start, "days": days, "items": items}
