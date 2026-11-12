"""Posters, trailers and where to watch, from TMDb (themoviedb.org), for the
Movies and Shows tabs (server-contract.md §6.54).

David, 9 Nov 2026: optional beside Trakt, whose items carry TMDb ids.
Credentials: MEDIA_TMDB_TOKEN (TMDb's v4 read access token, sent as a
Bearer; preferred) or MEDIA_TMDB_API_KEY (the v3 key, as `api_key`).
MEDIA_TMDB_REGION (ISO 3166-1, default AU) picks whose streaming services
"Where to watch" lists. Without a credential nothing here calls TMDb: the
lists carry no posters and the info sheet no trailer or services.

  posters   GET /movie/{id} | /tv/{id} → poster_path, kept a month on disk
            (`<state_dir>/tmdb-posters.json`), at most NEW_PER_LISTING new
            ones a listing (the rest come at the next)
  details   GET /movie|tv/{id}?append_to_response=videos,watch/providers →
            the best YouTube trailer (official Trailer > Trailer > official
            Teaser > Teaser) and the region's services, kept a day in memory

TMDb asks that it be credited where its data is shown ("This product uses
the TMDB API but is not endorsed or certified by TMDB"; JustWatch for the
services): §6.54 `attribution`.
"""

from __future__ import annotations

import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Optional

API = "https://api.themoviedb.org/3"
IMG = "https://image.tmdb.org/t/p"
POSTER_SIZE = "w342"
LOGO_SIZE = "w92"
POSTER_TTL_S = 30 * 86400.0
DETAILS_TTL_S = 86400.0
NEW_PER_LISTING = 40
ATTRIBUTION = ("Posters, trailers and services from TMDB (this product uses the TMDB API but is not "
               "endorsed or certified by TMDB); services by JustWatch.")

_lock = threading.Lock()
_details: dict[str, tuple[float, dict]] = {}


def token() -> str:
    return (os.environ.get("MEDIA_TMDB_TOKEN") or "").strip()


def api_key() -> str:
    return (os.environ.get("MEDIA_TMDB_API_KEY") or "").strip()


def region() -> str:
    return ((os.environ.get("MEDIA_TMDB_REGION") or "").strip() or "AU").upper()


def set_up() -> bool:
    return bool(token() or api_key())


def _request(path: str, params: Optional[dict] = None, timeout: float = 15.0) -> dict:
    """GET API+path; the parsed JSON. Raises on any failure (callers
    swallow it: TMDb only adds to a list)."""
    import urllib.parse
    import urllib.request
    params = dict(params or {})
    headers = {"accept": "application/json"}
    if token():
        headers["Authorization"] = f"Bearer {token()}"
    elif api_key():
        params["api_key"] = api_key()
    url = f"{API}{path}?{urllib.parse.urlencode(params)}" if params else API + path
    with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=timeout) as r:
        got = json.loads(r.read().decode() or "{}")
    return got if isinstance(got, dict) else {}


def _kind(item: dict) -> tuple[str, Optional[int]]:
    """("movie" | "tv", tmdb id) for a §6.54 item; an episode is its show's."""
    if item.get("kind") == "movie":
        return "movie", (item.get("ids") or {}).get("tmdb")
    if item.get("kind") == "episode":
        return "tv", (item.get("show_ids") or {}).get("tmdb")
    return "tv", (item.get("ids") or {}).get("tmdb")


def _posters_path() -> Path:
    from ._paths import state_dir
    return state_dir() / "tmdb-posters.json"


def _posters_load() -> dict:
    try:
        d = json.loads(_posters_path().read_text())
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _posters_save(d: dict) -> None:
    p = _posters_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(f".{p.name}.{os.getpid()}")
    tmp.write_text(json.dumps(d))
    os.replace(tmp, p)


def poster_url(path: str, size: str = POSTER_SIZE) -> Optional[str]:
    return f"{IMG}/{size}{path}" if path else None


def add_posters(items: list[dict], now: Optional[float] = None) -> None:
    """Set `poster` on each item TMDb has one for (in place)."""
    if not set_up():
        return
    now = time.time() if now is None else now
    kept = _posters_load()
    want = []
    for it in items:
        kind, tid = _kind(it)
        if tid is None:
            continue
        key = f"{kind}:{tid}"
        hit = kept.get(key)
        if not (isinstance(hit, dict) and now - float(hit.get("at") or 0) < POSTER_TTL_S):
            if key not in want:
                want.append(key)
    if want:
        def one(key: str):
            kind, _, tid = key.partition(":")
            try:
                return key, str(_request(f"/{kind}/{tid}").get("poster_path") or "")
            except Exception:  # noqa: BLE001 — asked again at a later listing
                return key, None
        with ThreadPoolExecutor(max_workers=6) as pool:
            for key, path in pool.map(one, want[:NEW_PER_LISTING]):
                if path is not None:
                    kept[key] = {"p": path, "at": now}
        _posters_save(kept)
    for it in items:
        kind, tid = _kind(it)
        hit = kept.get(f"{kind}:{tid}") if tid is not None else None
        if isinstance(hit, dict) and hit.get("p"):
            it["poster"] = poster_url(hit["p"])


def _trailer(videos: dict) -> Optional[str]:
    yt = [v for v in (videos or {}).get("results") or []
          if isinstance(v, dict) and v.get("site") == "YouTube" and v.get("key")]
    if not yt:
        return None

    def score(v):
        return {"Trailer": 4, "Teaser": 2}.get(v.get("type"), 0) + (1 if v.get("official") else 0)
    return f"https://www.youtube.com/watch?v={max(yt, key=score)['key']}"


def _where(providers: dict, reg: str) -> Optional[dict]:
    r = ((providers or {}).get("results") or {}).get(reg)
    if not isinstance(r, dict):
        return None
    out: dict = {"region": reg, "link": r.get("link") or None}
    for key in ("flatrate", "free", "ads", "rent", "buy"):
        out["stream" if key == "flatrate" else key] = [
            {"name": p.get("provider_name") or "", "logo": poster_url(p.get("logo_path") or "", LOGO_SIZE)}
            for p in r.get(key) or [] if isinstance(p, dict) and p.get("provider_name")]
    return out


def details(kind: str, tmdb_id: int, now: Optional[float] = None) -> dict:
    """`{trailer, where, poster, tagline}` for a movie or show (kind
    "movie" | "tv"). ValueError when not set up or the kind is wrong;
    other errors from TMDb pass through."""
    if not set_up():
        raise ValueError("no TMDb credential on this server (MEDIA_TMDB_TOKEN or MEDIA_TMDB_API_KEY)")
    if kind not in ("movie", "tv"):
        raise ValueError("kind must be movie or tv")
    now = time.time() if now is None else now
    reg = region()
    key = f"{kind}:{int(tmdb_id)}:{reg}"
    with _lock:
        hit = _details.get(key)
    if hit and now - hit[0] < DETAILS_TTL_S:
        return hit[1]
    got = _request(f"/{kind}/{int(tmdb_id)}", {"append_to_response": "videos,watch/providers"})
    out = {"trailer": _trailer(got.get("videos") or {}),
           "where": _where(got.get("watch/providers") or {}, reg),
           "poster": poster_url(str(got.get("poster_path") or "")),
           "tagline": str(got.get("tagline") or "") or None,
           "region": reg, "attribution": ATTRIBUTION}
    with _lock:
        _details[key] = (now, out)
    return out


def add_photos(people: list[dict], now: Optional[float] = None) -> None:
    """Set `photo` on each person TMDb has a picture of (in place; a row's
    `ids.tmdb`), from GET /person/{id}, kept with the posters."""
    if not set_up():
        return
    now = time.time() if now is None else now
    kept = _posters_load()
    want = []
    for p in people:
        tid = (p.get("ids") or {}).get("tmdb")
        hit = kept.get(f"person:{tid}") if tid is not None else None
        if tid is not None and not (isinstance(hit, dict) and now - float(hit.get("at") or 0) < POSTER_TTL_S):
            if f"person:{tid}" not in want:
                want.append(f"person:{tid}")
    if want:
        def one(key: str):
            try:
                return key, str(_request(f"/person/{key.partition(':')[2]}").get("profile_path") or "")
            except Exception:  # noqa: BLE001 — asked again later
                return key, None
        with ThreadPoolExecutor(max_workers=6) as pool:
            for key, path in pool.map(one, want[:NEW_PER_LISTING]):
                if path is not None:
                    kept[key] = {"p": path, "at": now}
        _posters_save(kept)
    for p in people:
        tid = (p.get("ids") or {}).get("tmdb")
        hit = kept.get(f"person:{tid}") if tid is not None else None
        if isinstance(hit, dict) and hit.get("p"):
            p["photo"] = poster_url(hit["p"], "w185")
