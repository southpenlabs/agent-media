"""Movies and TV from Trakt (trakt.tv), for the app's Movies and Shows
tabs (server-contract.md §6.54).

David, 9 Nov 2026: "let's add Movies and TV series tabs to the media tab",
with Trakt as the source. Read only: what he watched, his watchlist, and
for TV the next episode of the shows he is watching.

**Credentials.** A Trakt API app of the install owner's own
(https://trakt.tv/oauth/applications, or developer.trakt.tv):
MEDIA_TRAKT_CLIENT_ID, and MEDIA_TRAKT_CLIENT_SECRET only where Trakt
issued one. Trakt's new apps get no secret ("This app signs users in with
PKCE") and, in development, may only send the browser back to localhost.
Without a client ID nothing here calls Trakt and everything answers "not
set up".

**Sign-in**, two ways, chosen by whether there is a secret:

- *device* (a secret): `begin` asks Trakt for a code (POST
  /oauth/device/code) and keeps it; the person types the code at the
  verification URL; `finish` polls once (POST /oauth/device/token: 200
  signed in, 400 still waiting, 429 slow down, 404/409/410/418 invalid,
  used, expired, denied).
- *pkce* (no secret; Trakt's PKCE guide: "Do not send a client_secret"):
  `begin` makes a verifier, its S256 challenge and a state, and answers
  Trakt's authorize address (https://auth.trakt.tv/oauth/authorize) with
  the app's redirect (MEDIA_TRAKT_REDIRECT, default http://localhost).
  After signing in, the browser lands on that redirect with `code` and
  `state` — a page that does not load on a phone — and the person pastes
  the address (or the code alone) back; `finish(address)` checks the state
  and swaps the code (POST /oauth/token, authorization_code with the
  code_verifier).

The tokens are kept 0600 in `<state_dir>/trakt-token.json`. An access
token lasts a week; it is refreshed an hour before it ends (POST
/oauth/token, grant_type refresh_token, the secret only if there is one;
Trakt's refresh tokens are single use, so the new one is kept at once).

**Reading** (GET /sync/history/movies|episodes, /sync/watchlist/movies|
shows, /shows/{id}/progress/watched) is kept TTL_S in memory, so the app
may ask on every visit. `_request` is the one place that talks HTTP.
"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Optional

API = "https://api.trakt.tv"
SITE = "https://trakt.tv"
AUTHORIZE = "https://auth.trakt.tv/oauth/authorize"
OOB = "urn:ietf:wg:oauth:2.0:oob"
PKCE_PENDING_S = 900.0
USER_AGENT = "agent-media/1 (+https://github.com/southpenlabs/agent-media)"
TTL_S = 300.0
#: Refresh the access token this long before Trakt ends it.
REFRESH_EARLY_S = 3600.0
#: Up next asks Trakt about this many of the most recently watched shows.
UP_NEXT_SHOWS = 8
OVERVIEW_CHARS = 600

KINDS = ("movie", "tv")
LISTS = {"movie": ("recent", "watchlist"), "tv": ("recent", "next", "watchlist")}
NOT_SET_UP = ("This server has no Trakt API app: create one at https://trakt.tv/oauth/applications "
              "and set MEDIA_TRAKT_CLIENT_ID (and MEDIA_TRAKT_CLIENT_SECRET, if Trakt gave one) on the server.")

_lock = threading.Lock()
_cache: dict[str, tuple[float, object]] = {}


class TraktError(Exception):
    """Trakt answered something other than what was asked for, or not at all."""


def client_id() -> str:
    return (os.environ.get("MEDIA_TRAKT_CLIENT_ID") or "").strip()


def client_secret() -> str:
    return (os.environ.get("MEDIA_TRAKT_CLIENT_SECRET") or "").strip()


def set_up() -> bool:
    return bool(client_id())


def flow() -> str:
    """"device" with a client secret, else "pkce"."""
    return "device" if client_secret() else "pkce"


def redirect_uri() -> str:
    """Where Trakt sends the browser after a PKCE sign-in: the app's
    redirect URI as set at Trakt (a development app: localhost only)."""
    return (os.environ.get("MEDIA_TRAKT_REDIRECT") or "").strip() or "http://localhost"


# ---- the token file -------------------------------------------------------------------------


def token_path() -> Path:
    from ._paths import state_dir
    return state_dir() / "trakt-token.json"


def _load() -> dict:
    try:
        d = json.loads(token_path().read_text())
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(d: dict) -> None:
    p = token_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(f".{p.name}.{os.getpid()}")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(d, f)
    os.replace(tmp, p)


def signed_in() -> bool:
    """A cheap local look (for GET /features): set up and a token kept."""
    return set_up() and bool(_load().get("refresh_token"))


# ---- HTTP -----------------------------------------------------------------------------------


def _request(method: str, path: str, body: Optional[dict] = None, token: str = "",
             timeout: float = 20.0) -> tuple[int, object]:
    """`(status, parsed JSON or {})` for `method API+path`. Raises
    TraktError only when Trakt could not be reached."""
    import urllib.error
    import urllib.request
    headers = {"Content-Type": "application/json", "User-Agent": USER_AGENT,
               "trakt-api-version": "2", "trakt-api-key": client_id()}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(API + path, data, headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode() or "{}"
            code = r.status
    except urllib.error.HTTPError as e:
        raw = (e.read() or b"").decode("utf-8", "replace") or "{}"
        code = e.code
    except (OSError, ValueError) as e:
        raise TraktError(f"Trakt could not be reached: {e}") from e
    try:
        return code, json.loads(raw)
    except ValueError:
        return code, {}


# ---- signing in -----------------------------------------------------------------------------


def _pending_live(d: dict, now: float) -> Optional[dict]:
    p = d.get("pending")
    if isinstance(p, dict) and float(p.get("expires_at") or 0) > now and p.get("flow", "device") == flow():
        return p
    return None


def _shown_pending(p: dict, now: float) -> dict:
    out = {"flow": p.get("flow", "device"), "expires_in": int(float(p["expires_at"]) - now)}
    if out["flow"] == "pkce":
        out.update(url=p["url"], redirect_uri=p["redirect_uri"])
    else:
        out.update(user_code=p["user_code"], verification_url=p["verification_url"],
                   interval=int(p.get("interval") or 5))
    return out


def status(now: Optional[float] = None) -> dict:
    """`{set_up, signed_in, user, flow, pending}` for Settings; `reason` when not set up."""
    now = time.time() if now is None else now
    d = _load()
    out = {"set_up": set_up(), "signed_in": set_up() and bool(d.get("refresh_token")),
           "user": d.get("user") or None, "flow": flow(), "pending": None}
    p = _pending_live(d, now)
    if p and not out["signed_in"]:
        out["pending"] = _shown_pending(p, now)
    if not out["set_up"]:
        out["reason"] = NOT_SET_UP
    return out


def begin(now: Optional[float] = None) -> dict:
    """Start a sign-in and keep what it needs. Device: `{flow: "device",
    user_code, verification_url, expires_in, interval}`; PKCE: `{flow:
    "pkce", url, redirect_uri, expires_in}`. ValueError when not set up."""
    if not set_up():
        raise ValueError(NOT_SET_UP)
    now = time.time() if now is None else now
    if flow() == "pkce":
        import base64
        import hashlib
        import secrets
        import urllib.parse
        verifier = secrets.token_urlsafe(64)[:96]
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        state = secrets.token_urlsafe(24)
        q = urllib.parse.urlencode({"response_type": "code", "client_id": client_id(),
                                    "redirect_uri": redirect_uri(), "state": state,
                                    "code_challenge": challenge, "code_challenge_method": "S256"})
        p = {"flow": "pkce", "verifier": verifier, "state": state, "redirect_uri": redirect_uri(),
             "url": f"{AUTHORIZE}?{q}", "expires_at": now + PKCE_PENDING_S}
    else:
        code, got = _request("POST", "/oauth/device/code", {"client_id": client_id()})
        if code != 200 or not isinstance(got, dict) or not got.get("device_code"):
            raise TraktError(f"Trakt gave no sign-in code (HTTP {code})")
        p = {"flow": "device", "device_code": got["device_code"], "user_code": str(got.get("user_code") or ""),
             "verification_url": str(got.get("verification_url") or "https://trakt.tv/activate"),
             "expires_at": now + float(got.get("expires_in") or 600),
             "interval": int(got.get("interval") or 5)}
    d = _load()
    d["pending"] = p
    _save(d)
    return _shown_pending(p, now)


_DEVICE_ERRORS = {404: "that sign-in code is not one Trakt knows; start again",
                  409: "that sign-in code was already used; start again",
                  410: "that sign-in code ran out; start again",
                  418: "the sign-in was declined on Trakt"}


def _keep_tokens(d: dict, got: dict, now: float) -> None:
    d["access_token"] = got["access_token"]
    d["refresh_token"] = got.get("refresh_token") or d.get("refresh_token")
    created = float(got["created_at"]) if got.get("created_at") is not None else now
    d["expires_at"] = created + float(got.get("expires_in") or 7 * 86400)


def _signed_in_now(d: dict, got: dict, now: float) -> dict:
    d.pop("pending", None)
    _keep_tokens(d, got, now)
    d["user"] = ""
    try:
        c, me = _request("GET", "/users/settings", token=d["access_token"])
        if c == 200 and isinstance(me, dict):
            u = me.get("user") or {}
            d["user"] = str(u.get("name") or u.get("username") or "")
    except TraktError:
        pass  # the name is only for Settings
    d["signed_in_at"] = now
    _save(d)
    clear_cache()
    return {"signed_in": True, "user": d["user"] or None}


def _code_from(address: str, state: str) -> str:
    """The code in a pasted redirect address (its state checked), or the
    code alone. ValueError when it is neither, or the state is another's."""
    import hmac
    import urllib.parse
    a = (address or "").strip()
    if not a:
        raise ValueError("paste the address the browser was sent to after signing in to Trakt")
    if "code=" in a or a.startswith(("http://", "https://")) or "?" in a:
        q = urllib.parse.parse_qs(urllib.parse.urlsplit(a).query or a.partition("?")[2] or a)
        if q.get("error"):
            raise ValueError(f"Trakt said: {q['error'][0]}; start again")
        code = (q.get("code") or [""])[0]
        got_state = (q.get("state") or [""])[0]
        if not code:
            raise ValueError("that address has no code in it; paste the whole address")
        if got_state and not hmac.compare_digest(got_state, state):
            raise ValueError("that address is from another sign-in; start again")
        return code
    if any(c.isspace() for c in a) or len(a) > 512:
        raise ValueError("that is not a Trakt sign-in code")
    return a


def finish(address: str = "", now: Optional[float] = None) -> dict:
    """Device: poll Trakt once for the pending sign-in, `{signed_in: True,
    user}` or `{signed_in: False, pending: True, interval}` while the code
    waits. PKCE: swap the code in `address` (the redirect address pasted
    back, or the code) for tokens. ValueError when there is no sign-in
    waiting, or Trakt refused it."""
    if not set_up():
        raise ValueError(NOT_SET_UP)
    now = time.time() if now is None else now
    d = _load()
    p = _pending_live(d, now)
    if not p:
        d.pop("pending", None)
        _save(d)
        raise ValueError("no sign-in is waiting (or it ran out); start again")
    if p.get("flow") == "pkce":
        code_ = _code_from(address, p["state"])
        code, got = _request("POST", "/oauth/token",
                             {"code": code_, "client_id": client_id(), "code_verifier": p["verifier"],
                              "redirect_uri": p["redirect_uri"], "grant_type": "authorization_code"})
        if code == 200 and isinstance(got, dict) and got.get("access_token"):
            return _signed_in_now(d, got, now)
        if code in (400, 401):
            d.pop("pending", None)
            _save(d)
            raise ValueError("Trakt did not take that code (used, or too old); start again")
        raise TraktError(f"Trakt answered HTTP {code}")
    code, got = _request("POST", "/oauth/device/token",
                         {"code": p["device_code"], "client_id": client_id(),
                          "client_secret": client_secret()})
    if code == 200 and isinstance(got, dict) and got.get("access_token"):
        return _signed_in_now(d, got, now)
    if code in (400, 429):
        interval = int(p.get("interval") or 5)
        if code == 429:
            p["interval"] = interval = interval + 1
            _save(d)
        return {"signed_in": False, "pending": True, "interval": interval}
    if code in _DEVICE_ERRORS:
        d.pop("pending", None)
        _save(d)
        raise ValueError(_DEVICE_ERRORS[code])
    raise TraktError(f"Trakt answered HTTP {code}")


def sign_out() -> None:
    """Forget the tokens (and tell Trakt, if it listens)."""
    d = _load()
    tok = d.get("access_token")
    if tok and set_up():
        body = {"token": tok, "client_id": client_id()}
        if client_secret():
            body["client_secret"] = client_secret()
        try:
            _request("POST", "/oauth/revoke", body, timeout=8)
        except TraktError:
            pass
    for k in ("access_token", "refresh_token", "expires_at", "user", "signed_in_at", "pending"):
        d.pop(k, None)
    _save(d)
    clear_cache()


def _access(now: float, force: bool = False) -> str:
    d = _load()
    if not d.get("refresh_token"):
        raise ValueError("not signed in to Trakt")
    if not force and d.get("access_token") and float(d.get("expires_at") or 0) - now > REFRESH_EARLY_S:
        return d["access_token"]
    body = {"refresh_token": d["refresh_token"], "client_id": client_id(),
            "redirect_uri": redirect_uri() if flow() == "pkce" else OOB, "grant_type": "refresh_token"}
    if client_secret():
        body["client_secret"] = client_secret()
    code, got = _request("POST", "/oauth/token", body)
    if code == 200 and isinstance(got, dict) and got.get("access_token"):
        _keep_tokens(d, got, now)
        _save(d)
        return d["access_token"]
    if code in (400, 401):
        # The refresh token is gone (revoked, or used twice): signed out.
        for k in ("access_token", "refresh_token", "expires_at"):
            d.pop(k, None)
        _save(d)
        raise ValueError("Trakt signed this server out; connect Trakt again in Settings")
    raise TraktError(f"Trakt answered HTTP {code} to a token refresh")


def _get(path: str, now: float) -> object:
    code, got = _request("GET", path, token=_access(now))
    if code == 401:
        code, got = _request("GET", path, token=_access(now, force=True))
    if code != 200:
        raise TraktError(f"Trakt answered HTTP {code} for {path.partition('?')[0]}")
    return got


#: Keys of what is the same for everyone (Trakt's public lists, its genres):
#: kept through a write, which changes only this account's.
_PUBLIC = ("d:", "g:")


def clear_cache(everything: bool = True) -> None:
    with _lock:
        if everything:
            _cache.clear()
        else:
            for k in [k for k in _cache if not k.startswith(_PUBLIC)]:
                del _cache[k]


def _cached(key: str, now: float, fn, ttl: float = TTL_S):
    with _lock:
        hit = _cache.get(key)
    if hit and now - hit[0] < ttl:
        return hit[1]
    value = fn()
    with _lock:
        _cache[key] = (now, value)
    return value


# ---- items ----------------------------------------------------------------------------------


def _ids(o: dict) -> dict:
    ids = o.get("ids") or {}
    return {k: ids.get(k) for k in ("trakt", "slug", "tmdb", "imdb") if ids.get(k) is not None}


def _genres(o: dict) -> list[str]:
    """Trakt's genre slugs as words: "science-fiction" → "Science Fiction"."""
    return [" ".join(w.capitalize() for w in str(g).replace("_", "-").split("-"))
            for g in o.get("genres") or [] if g]


def _short(text) -> str:
    t = " ".join(str(text or "").split())
    return t if len(t) <= OVERVIEW_CHARS else t[:OVERVIEW_CHARS - 1].rstrip() + "…"


def _movie(m: dict) -> dict:
    ids = _ids(m)
    return {"id": f"movie:{ids.get('trakt') or ids.get('slug')}", "kind": "movie",
            "title": str(m.get("title") or ""), "year": m.get("year"),
            "runtime": m.get("runtime") or None, "genres": _genres(m),
            "overview": _short(m.get("overview")),
            "url": f"{SITE}/movies/{ids.get('slug') or ids.get('trakt')}", "ids": ids}


def _show(s: dict) -> dict:
    ids = _ids(s)
    return {"id": f"show:{ids.get('trakt') or ids.get('slug')}", "kind": "show",
            "title": str(s.get("title") or ""), "year": s.get("year"),
            "runtime": s.get("runtime") or None, "genres": _genres(s),
            "overview": _short(s.get("overview")),
            "url": f"{SITE}/shows/{ids.get('slug') or ids.get('trakt')}", "ids": ids}


def _episode(e: dict, s: dict) -> dict:
    ids, show_ids = _ids(e), _ids(s)
    season, number = e.get("season"), e.get("number")
    slug = show_ids.get("slug") or show_ids.get("trakt")
    return {"id": f"episode:{ids.get('trakt') or f'{slug}-{season}-{number}'}", "kind": "episode",
            "title": str(e.get("title") or ""), "show": str(s.get("title") or ""),
            "year": s.get("year"), "season": season, "number": number,
            "runtime": e.get("runtime") or s.get("runtime") or None,
            "genres": _genres(s),
            "overview": _short(e.get("overview") or s.get("overview")),
            "url": f"{SITE}/shows/{slug}/seasons/{season}/episodes/{number}",
            "ids": ids, "show_ids": show_ids}


def _history(rows, make) -> list[dict]:
    """One item per thing watched, its latest watch first, with `plays`."""
    out: dict[str, dict] = {}
    for r in rows if isinstance(rows, list) else []:
        if not isinstance(r, dict):
            continue
        item = make(r)
        if not item:
            continue
        seen = out.get(item["id"])
        if seen:
            seen["plays"] += 1
            continue
        item["watched_at"] = r.get("watched_at")
        item["plays"] = 1
        out[item["id"]] = item
    return list(out.values())


def _movies_recent(now: float) -> list[dict]:
    rows = _cached("h:movies", now, lambda: _get("/sync/history/movies?extended=full&limit=100", now))
    return _history(rows, lambda r: _movie(r["movie"]) if isinstance(r.get("movie"), dict) else None)


def _episode_rows(now: float):
    return _cached("h:episodes", now, lambda: _get("/sync/history/episodes?extended=full&limit=200", now))


def _tv_recent(now: float) -> list[dict]:
    return _history(_episode_rows(now), lambda r: _episode(r["episode"], r.get("show") or {})
                    if isinstance(r.get("episode"), dict) else None)


def _watchlist(kind: str, now: float) -> list[dict]:
    path = "/sync/watchlist/movies?extended=full" if kind == "movie" else "/sync/watchlist/shows?extended=full"
    rows = _cached(f"w:{kind}", now, lambda: _get(path, now))
    out = []
    for r in rows if isinstance(rows, list) else []:
        o = r.get("movie" if kind == "movie" else "show") if isinstance(r, dict) else None
        if not isinstance(o, dict):
            continue
        item = _movie(o) if kind == "movie" else _show(o)
        item["listed_at"] = r.get("listed_at")
        out.append(item)
    return out


def _up_next(now: float) -> list[dict]:
    shows: list[dict] = []
    seen = set()
    rows = _episode_rows(now)
    for r in rows if isinstance(rows, list) else []:
        s = r.get("show") if isinstance(r, dict) else None
        tid = ((s or {}).get("ids") or {}).get("trakt")
        if tid is None or tid in seen:
            continue
        seen.add(tid)
        shows.append(s)
        if len(shows) >= UP_NEXT_SHOWS:
            break
    out = []
    for s in shows:
        tid = s["ids"]["trakt"]
        try:
            prog = _cached(f"p:{tid}", now, lambda tid=tid: _get(
                f"/shows/{tid}/progress/watched?hidden=false&specials=false&count_specials=false&extended=full",
                now))
        except TraktError:
            continue  # one show unread, the rest still listed
        nxt = prog.get("next_episode") if isinstance(prog, dict) else None
        if not isinstance(nxt, dict):
            continue
        item = _episode(nxt, s)
        item["watched_at"] = prog.get("last_watched_at")
        aired, done = prog.get("aired"), prog.get("completed")
        if isinstance(aired, int) and isinstance(done, int):
            item["left"] = max(0, aired - done)
        out.append(item)
    return out


def listing(kind: str, which: str, now: Optional[float] = None) -> dict:
    """What GET /watch answers (less `ok`). ValueError for a kind or list
    that is not one; TraktError when Trakt fails."""
    if kind not in KINDS:
        raise ValueError("kind must be movie or tv")
    which = which or "recent"
    if which not in LISTS[kind]:
        raise ValueError(f"list must be one of {', '.join(LISTS[kind])}")
    now = time.time() if now is None else now
    out = {"kind": kind, "list": which, "lists": list(LISTS[kind]), "set_up": set_up(),
           "signed_in": signed_in(), "items": [], "genres": []}
    if not out["set_up"]:
        out["reason"] = NOT_SET_UP
        return out
    if not out["signed_in"]:
        out["reason"] = "Not connected to Trakt: connect it in Settings."
        return out
    try:
        if which == "watchlist":
            items = _watchlist(kind, now)
        elif kind == "movie":
            items = _movies_recent(now)
        elif which == "next":
            items = _up_next(now)
        else:
            items = _tv_recent(now)
    except ValueError as e:  # signed out by Trakt
        out["signed_in"] = False
        out["reason"] = str(e)
        return out
    _mark_watchlist(kind, items, now)
    from . import tmdb
    if tmdb.set_up():
        try:
            tmdb.add_posters(items)
        except Exception as e:  # noqa: BLE001 — the list without its pictures
            import sys
            print(f"trakt: posters: {e}", file=sys.stderr)
    out["items"] = items
    out["genres"] = sorted({g for i in items for g in i.get("genres") or []})
    return out


# ---- writing (David, 9 Nov 2026: "Add those buttons") ---------------------------------------
#
# Marking watched, taking a watch back, and the watchlist, from the app, so
# Trakt's own app is not needed. Each write empties the lists kept above, so
# the next listing shows it at once.

ACTIONS = ("watched", "unwatch", "watchlist", "unwatchlist")
_PLURAL = {"movie": "movies", "episode": "episodes", "show": "shows"}


def _post(path: str, body: dict, now: float) -> dict:
    code, got = _request("POST", path, body, token=_access(now))
    if code == 401:
        code, got = _request("POST", path, body, token=_access(now, force=True))
    if code not in (200, 201):
        raise TraktError(f"Trakt answered HTTP {code} for {path}")
    return got if isinstance(got, dict) else {}


def _count(part) -> int:
    return sum(v for v in (part or {}).values() if isinstance(v, int)) if isinstance(part, dict) else 0


def _missing(got: dict) -> bool:
    nf = got.get("not_found") or {}
    return any(isinstance(v, list) and v for v in nf.values())


def _target(kind: str, ids: dict, season=None, watched_at=None) -> dict:
    """The sync body for one movie, episode, show or a show's season."""
    clean = {k: ids[k] for k in ("trakt", "slug", "tmdb", "imdb") if isinstance(ids, dict) and ids.get(k)}
    if not clean:
        raise ValueError("which one: ids.trakt (or slug, tmdb, imdb)")
    at = {"watched_at": watched_at} if watched_at else {}
    if kind == "season":
        if not isinstance(season, int) or season < 0:
            raise ValueError("a season needs its number")
        return {"shows": [{"ids": clean, "seasons": [{"number": season, **at}]}]}
    if kind not in _PLURAL:
        raise ValueError("kind must be movie, episode, show or season")
    return {_PLURAL[kind]: [{"ids": clean, **at}]}


def _last_history_id(kind: str, ids: dict, now: float) -> Optional[int]:
    """The newest history row of a movie or episode just marked, so Undo
    takes back that watch only (not earlier ones)."""
    if kind not in ("movie", "episode") or not ids.get("trakt"):
        return None
    try:
        rows = _get(f"/sync/history/{_PLURAL[kind]}/{ids['trakt']}?limit=1", now)
        if isinstance(rows, list) and rows and isinstance(rows[0], dict):
            return rows[0].get("id")
    except TraktError:
        pass
    return None


def write(body: dict, now: Optional[float] = None) -> dict:
    """POST /watch: `{"action": watched|unwatch|watchlist|unwatchlist,
    "kind": movie|episode|show|season, "ids", "season"?, "watched_at"?}`,
    or `{"action": "unwatch", "history_ids": [...]}` (a watch's Undo).
    `{action, changed, not_found, undo?}`. ValueError for a bad body or when
    not signed in; TraktError when Trakt fails."""
    if not signed_in():
        raise ValueError("not connected to Trakt: connect it in Settings")
    now = time.time() if now is None else now
    action = str(body.get("action") or "")
    if action not in ACTIONS:
        raise ValueError(f"action must be one of {', '.join(ACTIONS)}")
    kind = str(body.get("kind") or "")
    ids = body.get("ids") if isinstance(body.get("ids"), dict) else {}
    hist = body.get("history_ids")
    try:
        if action == "unwatch" and isinstance(hist, list) and hist:
            hids = [int(h) for h in hist][:50]
            got = _post("/sync/history/remove", {"ids": hids}, now)
            return {"action": action, "changed": _count(got.get("deleted")), "not_found": _missing(got)}
        watched_at = body.get("watched_at") or None
        if watched_at is not None and not isinstance(watched_at, str):
            raise ValueError("watched_at is an ISO time")
        if action in ("watchlist", "unwatchlist") and kind not in ("movie", "show", "episode"):
            raise ValueError("a watchlist takes a movie, a show or an episode")
        target = _target(kind, ids, body.get("season"), watched_at if action == "watched" else None)
        path = {"watched": "/sync/history", "unwatch": "/sync/history/remove",
                "watchlist": "/sync/watchlist", "unwatchlist": "/sync/watchlist/remove"}[action]
        got = _post(path, target, now)
        part = got.get("added") if action in ("watched", "watchlist") else got.get("deleted")
        out = {"action": action, "changed": _count(part), "not_found": _missing(got)}
        if action == "watchlist":
            out["changed"] += _count(got.get("existing"))
        if action == "watched":
            hid = _last_history_id(kind, ids, now)
            out["undo"] = ({"action": "unwatch", "history_ids": [hid]} if hid is not None else
                           {"action": "unwatch", "kind": kind, "ids": ids,
                            **({"season": body.get("season")} if kind == "season" else {})})
        return out
    finally:
        clear_cache(everything=False)


def search(kind: str, query: str, now: Optional[float] = None) -> list[dict]:
    """GET /watch/search: movies or shows on Trakt matching `query`, each
    with `on_watchlist` and a poster where TMDb is set up."""
    if kind not in KINDS:
        raise ValueError("kind must be movie or tv")
    q = " ".join(str(query or "").split())[:120]
    if len(q) < 2:
        raise ValueError("type at least two letters")
    if not signed_in():
        raise ValueError("not connected to Trakt: connect it in Settings")
    import urllib.parse
    now = time.time() if now is None else now
    what = "movie" if kind == "movie" else "show"
    rows = _cached(f"s:{what}:{q.lower()}", now, lambda: _get(
        f"/search/{what}?query={urllib.parse.quote(q)}&extended=full&limit=20", now))
    items = []
    for r in rows if isinstance(rows, list) else []:
        o = r.get(what) if isinstance(r, dict) else None
        if isinstance(o, dict):
            items.append(_movie(o) if what == "movie" else _show(o))
    _mark_watchlist(kind, items, now)
    from . import tmdb
    if tmdb.set_up():
        try:
            tmdb.add_posters(items)
        except Exception:  # noqa: BLE001 — the results without their pictures
            pass
    return items


def _mark_watchlist(kind: str, items: list[dict], now: float) -> None:
    """`on_watchlist` on each item (an episode: its show's)."""
    try:
        listed = {i["ids"].get("trakt") for i in _watchlist(kind, now)}
    except (TraktError, ValueError):
        return
    for i in items:
        tid = ((i.get("show_ids") if i.get("kind") == "episode" else i.get("ids")) or {}).get("trakt")
        i["on_watchlist"] = tid is not None and tid in listed


def show(trakt_id: int, now: Optional[float] = None) -> dict:
    """GET /watch/show: a show's seasons as watched so far, for its sheet's
    Mark a season watched. `{seasons: [{number, aired, completed}], left}`."""
    if not signed_in():
        raise ValueError("not connected to Trakt: connect it in Settings")
    now = time.time() if now is None else now
    tid = int(trakt_id)
    prog = _cached(f"p:{tid}", now, lambda: _get(
        f"/shows/{tid}/progress/watched?hidden=false&specials=false&count_specials=false&extended=full", now))
    seasons = []
    for s in (prog.get("seasons") if isinstance(prog, dict) else None) or []:
        if isinstance(s, dict) and isinstance(s.get("number"), int) and s["number"] > 0:
            seasons.append({"number": s["number"], "aired": int(s.get("aired") or 0),
                            "completed": int(s.get("completed") or 0)})
    aired = prog.get("aired") if isinstance(prog, dict) else None
    done = prog.get("completed") if isinstance(prog, dict) else None
    return {"seasons": seasons,
            "left": max(0, aired - done) if isinstance(aired, int) and isinstance(done, int) else None}


# ---- discovering (David, 10 Nov 2026: "browse and search that movie and shows database of
# items not yet marked in trakt") -------------------------------------------------------------
#
# Trakt's trending, popular and most anticipated lists (public: the client ID
# only, kept an hour) and its recommendations for this account (kept a
# quarter of an hour), each item marked `watched` and `on_watchlist`, and by
# default without what was already watched.

DISCOVER = ("trending", "popular", "recommended", "anticipated")
PUBLIC_TTL_S = 3600.0
RECOMMENDED_TTL_S = 900.0
GENRES_TTL_S = 86400.0
WATCHED_TTL_S = 900.0
PAGE_SIZE = 20


def _public_get(path: str) -> object:
    code, got = _request("GET", path)
    if code != 200:
        raise TraktError(f"Trakt answered HTTP {code} for {path.partition('?')[0]}")
    return got


def genres(kind: str, now: Optional[float] = None) -> list[dict]:
    """Trakt's genres for movies or shows, `[{slug, name}]`, kept a day."""
    now = time.time() if now is None else now
    what = "movies" if kind == "movie" else "shows"
    rows = _cached(f"g:{what}", now, lambda: _public_get(f"/genres/{what}"), GENRES_TTL_S)
    out = []
    for g in rows if isinstance(rows, list) else []:
        if isinstance(g, dict) and g.get("slug") and g["slug"] != "none":
            out.append({"slug": str(g["slug"]), "name": _genres({"genres": [g["slug"]]})[0]})
    return sorted(out, key=lambda g: g["name"])


def _watched_ids(kind: str, now: float) -> set:
    """The Trakt ids of every movie (or show, any episode of it) this account
    has watched: GET /sync/watched/movies|shows, kept a quarter of an hour."""
    what = "movies" if kind == "movie" else "shows"
    path = f"/sync/watched/{what}" + ("?extended=noseasons" if what == "shows" else "")
    rows = _cached(f"wd:{what}", now, lambda: _get(path, now), WATCHED_TTL_S)
    one = "movie" if kind == "movie" else "show"
    return {((r.get(one) or {}).get("ids") or {}).get("trakt") for r in rows if isinstance(r, dict)} \
        if isinstance(rows, list) else set()


def _years(year: str) -> Optional[tuple[int, int]]:
    y = str(year or "").strip()
    if not y:
        return None
    a, _, b = y.partition("-")
    try:
        lo, hi = int(a), int(b or a)
    except ValueError:
        raise ValueError("year is a year (2024) or a span (1990-1999)") from None
    if not (1870 <= lo <= hi <= 2100):
        raise ValueError("year is a year (2024) or a span (1990-1999)")
    return lo, hi


def discover(kind: str, which: str, genre: str = "", year: str = "", page: int = 1,
             hide_watched: bool = True, now: Optional[float] = None) -> dict:
    """What GET /watch/discover answers (less `ok`). ValueError for a bad
    kind, list, genre or year, or recommendations when not signed in;
    TraktError when Trakt fails."""
    if kind not in KINDS:
        raise ValueError("kind must be movie or tv")
    which = which or "trending"
    if which not in DISCOVER:
        raise ValueError(f"list must be one of {', '.join(DISCOVER)}")
    if not set_up():
        raise ValueError(NOT_SET_UP)
    me = signed_in()
    if which == "recommended" and not me:
        raise ValueError("recommendations need Trakt connected: connect it in Settings")
    genre = str(genre or "").strip().lower()
    if genre and not all(c.isalnum() or c == "-" for c in genre):
        raise ValueError("genre is one of the genres' slugs")
    span = _years(year)
    page = max(1, min(int(page or 1), 50))
    now = time.time() if now is None else now
    what = "movies" if kind == "movie" else "shows"
    one = "movie" if kind == "movie" else "show"
    import urllib.parse
    if which == "recommended":
        # Trakt gives up to 100, unpaged and unfiltered: filtered and paged here.
        rows = _cached(f"r:{what}", now, lambda: _get(
            f"/recommendations/{what}?extended=full&limit=100&ignore_collected=false", now), RECOMMENDED_TTL_S)
        raw = [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []
        objs = [r.get(one) if isinstance(r.get(one), dict) else r for r in raw]
        if genre:
            objs = [o for o in objs if genre in [str(g).lower() for g in o.get("genres") or []]]
        if span:
            objs = [o for o in objs if isinstance(o.get("year"), int) and span[0] <= o["year"] <= span[1]]
        start = (page - 1) * PAGE_SIZE
        more = len(objs) > start + PAGE_SIZE
        objs = objs[start:start + PAGE_SIZE]
    else:
        q = {"extended": "full", "page": page, "limit": PAGE_SIZE}
        if genre:
            q["genres"] = genre
        if span:
            q["years"] = f"{span[0]}-{span[1]}" if span[0] != span[1] else str(span[0])
        path = f"/{what}/{which}?{urllib.parse.urlencode(q)}"
        rows = _cached(f"d:{path}", now, lambda: _public_get(path), PUBLIC_TTL_S)
        raw = [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []
        # Trending and anticipated wrap each in {watchers|list_count, movie}; popular does not.
        objs = [r.get(one) if isinstance(r.get(one), dict) else r for r in raw]
        more = len(raw) >= PAGE_SIZE
    items = [_movie(o) if kind == "movie" else _show(o) for o in objs if isinstance(o, dict) and o.get("ids")]
    if me:
        try:
            seen = _watched_ids(kind, now)
        except (TraktError, ValueError):
            seen = set()
        for i in items:
            i["watched"] = i["ids"].get("trakt") in seen
        _mark_watchlist(kind, items, now)
        if hide_watched:
            items = [i for i in items if not i["watched"]]
    from . import tmdb
    if tmdb.set_up():
        try:
            tmdb.add_posters(items)
        except Exception:  # noqa: BLE001 — the list without its pictures
            pass
    try:
        gs = genres(kind, now)
    except TraktError:
        gs = []
    return {"kind": kind, "list": which, "lists": [d for d in DISCOVER if d != "recommended" or me],
            "page": page, "more": more, "hide_watched": hide_watched and me, "signed_in": me,
            "items": items, "genres": gs}
