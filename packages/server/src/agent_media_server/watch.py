"""Movies and TV from Trakt: the app's Movies and Shows tabs and the Trakt
sign-in in Settings (server-contract.md §6.54; agent_media_core.trakt and
agent_media_core.tmdb; David, 9 Nov 2026).

  GET  /watch?kind=movie|tv&list=recent|watchlist|next   gated →
           {"ok", "kind", "list", "lists", "set_up", "signed_in", "items", "genres",
            "reason"?, "tmdb"}
  GET  /watch/item?kind=movie|tv&tmdb=<id>                gated →
           {"ok", "trailer", "where", "poster", "tagline", "region", "attribution"}
  POST /watch {"action": watched|unwatch|watchlist|unwatchlist, "kind": movie|episode|show|season,
               "ids", "season"?, "watched_at"?} | {"action": "unwatch", "history_ids"}   admins →
           {"ok", "action", "changed", "not_found", "undo"?}
  GET  /watch/search?kind=movie|tv&query=   gated → {"ok", "items"}
  GET  /watch/discover?kind=movie|tv&list=trending|popular|recommended|anticipated&genre=&year=&page=
       &hide_watched=0|1   gated → {"ok", "kind", "list", "lists", "page", "more", "hide_watched",
                           "signed_in", "items", "genres": [{slug, name}]}
  GET  /watch/search?kind=all&query=        gated → {"ok", "q", "movies", "shows", "people", "lists"}
  GET  /watch/title?kind=movie|tv&id=       gated → a title's page (facts, trailer, ratings, cast, crew, related, seasons)
  GET  /watch/season?id=<show>&season=<n>   gated → {"ok", "season", "episodes"}
  GET  /watch/person?id=                    gated → {"ok", "person", "movies": {cast, crew}, "shows": {cast, crew}}
  GET  /watch/lists?which=trending|popular|mine&page=    gated → {"ok", "lists", …}
  GET  /watch/list?id=&mine=0|1&page=       gated → {"ok", "items", "page", "more"}
  GET  /watch/calendar?kind=tv|movie&days=  gated → {"ok", "items", "from", "days"}
  GET  /watch/show?id=<trakt id>            gated → {"ok", "seasons": [{number, aired, completed}], "left"}
  GET  /accounts/trakt              admins → {"ok", "set_up", "signed_in", "user", "flow", "pending", "reason"?}
  POST /accounts/trakt/start        admins → device {"ok", "flow", "user_code", "verification_url",
                                    "expires_in", "interval"} | pkce {"ok", "flow", "url", "redirect_uri",
                                    "expires_in"}
  POST /accounts/trakt/finish       admins {"address"?} → {"ok", "signed_in", "user"?, "pending"?, "interval"?}
                                    (pkce: the redirect address pasted back, or its code)
  POST /accounts/trakt/signout      admins → {"ok", "signed_in": false, …}
"""

from __future__ import annotations

import sys
import urllib.parse
from http.server import BaseHTTPRequestHandler

from . import auth

PATHS = frozenset({"/watch", "/watch/item", "/watch/search", "/watch/show", "/watch/discover", "/watch/title",
                   "/watch/season", "/watch/person", "/watch/lists", "/watch/list", "/watch/calendar", "/accounts/trakt", "/accounts/trakt/start",
                   "/accounts/trakt/finish", "/accounts/trakt/signout"})


#: Pages, people, lists and the calendar (trakt_browse.py), all GET and gated.
BROWSE = ("/watch/title", "/watch/season", "/watch/person", "/watch/lists", "/watch/list", "/watch/calendar")
FILTERS = ("rating", "runtime", "language", "country", "certification", "network", "status")


def _q(h: BaseHTTPRequestHandler, name: str) -> str:
    return (urllib.parse.parse_qs(h.path.partition("?")[2]).get(name) or [""])[0].strip()


def _body(h: BaseHTTPRequestHandler) -> dict:
    import json
    n = int(h.headers.get("Content-Length") or 0)
    try:
        body = json.loads(h.rfile.read(n) or b"{}") if n else {}
    except ValueError:
        body = {}
    return body if isinstance(body, dict) else {}


def handle(h: BaseHTTPRequestHandler, method: str, path: str, bearer: str, send_json) -> bool:
    from agent_media_core import tmdb, trakt

    if path == "/watch" and method == "POST":
        user, err = auth.may_admin(bearer)
        if not user:
            send_json(h, err.pop("status", 403), {"ok": False, **err})
            return True
        try:
            got = trakt.write(_body(h))
        except ValueError as e:
            send_json(h, 400, {"ok": False, "error": str(e)})
            return True
        except Exception as e:  # noqa: BLE001
            print(f"watch: write: {e}", file=sys.stderr)
            send_json(h, 502, {"ok": False, "error": f"Trakt didn't answer: {e}"})
            return True
        send_json(h, 200, {"ok": True, **got})
        return True
    if path in BROWSE:
        if method != "GET":
            return False
        user, err = auth.gate(bearer)
        if not user:
            send_json(h, err.pop("status", 401), {"ok": False, **err})
            return True
        from agent_media_core import trakt_browse as B
        page = _q(h, "page") or "1"
        try:
            if not page.isdigit():
                raise ValueError("page is a number")
            if path == "/watch/title":
                got = B.title(_q(h, "kind"), _q(h, "id"))
                got["tmdb"] = tmdb.set_up()
            elif path == "/watch/season":
                got = B.season(_q(h, "id"), _q(h, "season"))
            elif path == "/watch/person":
                got = B.person(_q(h, "id"))
            elif path == "/watch/lists":
                got = B.lists(_q(h, "which") or "trending", int(page))
            elif path == "/watch/list":
                got = B.list_items(_q(h, "id"), _q(h, "mine") == "1", int(page))
            else:
                days = _q(h, "days") or "14"
                if not days.isdigit():
                    raise ValueError("days is a number")
                got = B.calendar(_q(h, "kind"), int(days))
        except ValueError as e:
            send_json(h, 400, {"ok": False, "error": str(e)})
            return True
        except LookupError as e:
            send_json(h, 404, {"ok": False, "error": str(e)})
            return True
        except Exception as e:  # noqa: BLE001
            print(f"watch: {path}: {e}", file=sys.stderr)
            send_json(h, 502, {"ok": False, "error": f"Trakt didn't answer: {e}"})
            return True
        send_json(h, 200, {"ok": True, **got})
        return True
    if path in ("/watch/search", "/watch/show", "/watch/discover"):
        if method != "GET":
            return False
        user, err = auth.gate(bearer)
        if not user:
            send_json(h, err.pop("status", 401), {"ok": False, **err})
            return True
        try:
            if path == "/watch/search" and _q(h, "kind") == "all":
                from agent_media_core import trakt_browse as B
                got = B.search_all(_q(h, "query"))
            elif path == "/watch/search":
                got = {"items": trakt.search(_q(h, "kind"), _q(h, "query"))}
            elif path == "/watch/discover":
                page = _q(h, "page") or "1"
                if not page.isdigit():
                    raise ValueError("page is a number")
                got = trakt.discover(_q(h, "kind"), _q(h, "list"), _q(h, "genre"), _q(h, "year"),
                                     int(page), _q(h, "hide_watched") != "0",
                                     filters={k: _q(h, k) for k in FILTERS if _q(h, k)})
            else:
                got = trakt.show(int(_q(h, "id") or "x"))
        except ValueError as e:
            send_json(h, 400, {"ok": False, "error": str(e)})
            return True
        except Exception as e:  # noqa: BLE001
            print(f"watch: {e}", file=sys.stderr)
            send_json(h, 502, {"ok": False, "error": f"Trakt didn't answer: {e}"})
            return True
        send_json(h, 200, {"ok": True, **got})
        return True
    if path in ("/watch", "/watch/item"):
        if method != "GET":
            return False
        user, err = auth.gate(bearer)
        if not user:
            send_json(h, err.pop("status", 401), {"ok": False, **err})
            return True
        if path == "/watch":
            try:
                got = trakt.listing(_q(h, "kind"), _q(h, "list"))
            except ValueError as e:
                send_json(h, 400, {"ok": False, "error": str(e)})
                return True
            except Exception as e:  # noqa: BLE001 — TraktError, or anything under it
                print(f"watch: {e}", file=sys.stderr)
                send_json(h, 502, {"ok": False, "error": f"Trakt didn't answer: {e}"})
                return True
            send_json(h, 200, {"ok": True, **got, "tmdb": tmdb.set_up()})
            return True
        try:
            tid = int(_q(h, "tmdb"))
            got = tmdb.details(_q(h, "kind"), tid)
        except ValueError as e:
            send_json(h, 400 if tmdb.set_up() else 404, {"ok": False, "error": str(e)})
            return True
        except Exception as e:  # noqa: BLE001
            print(f"watch: tmdb: {e}", file=sys.stderr)
            send_json(h, 502, {"ok": False, "error": "TMDb didn't answer"})
            return True
        send_json(h, 200, {"ok": True, **got})
        return True

    if path == "/accounts/trakt" and method != "GET":
        return False
    if path != "/accounts/trakt" and method != "POST":
        return False
    user, err = auth.may_admin(bearer)
    if not user:
        send_json(h, err.pop("status", 403), {"ok": False, **err})
        return True
    try:
        if path == "/accounts/trakt":
            send_json(h, 200, {"ok": True, **trakt.status()})
        elif path.endswith("/start"):
            send_json(h, 200, {"ok": True, **trakt.begin()})
        elif path.endswith("/finish"):
            import json
            n = int(h.headers.get("Content-Length") or 0)
            try:
                body = json.loads(h.rfile.read(n) or b"{}") if n else {}
            except ValueError:
                body = {}
            address = str((body if isinstance(body, dict) else {}).get("address") or "")
            send_json(h, 200, {"ok": True, **trakt.finish(address)})
        else:
            trakt.sign_out()
            send_json(h, 200, {"ok": True, **trakt.status()})
    except ValueError as e:
        send_json(h, 409 if not trakt.set_up() else 400,
                  {"ok": False, "error": str(e), "set_up": trakt.set_up()})
    except Exception as e:  # noqa: BLE001
        print(f"watch: trakt sign-in: {e}", file=sys.stderr)
        send_json(h, 502, {"ok": False, "error": f"Trakt didn't answer: {e}"})
    return True
