"""Podcasts to find and follow (server-contract.md §6.9a "Podcasts: search,
browse, follow"; agent_media_core.podcast_discover; David, 11 Nov 2026).

  GET  /podcasts/discover?list=top|following|new|search|author&q=&genre=&page=   gated
  GET  /podcasts/show?itunes=<id>|feed=<url>&q=&page=                            gated
  POST /podcasts/show {"action": "follow", "feed", "title"?, "author"?, "art"?, "itunes"?}   gated
                      {"action": "unfollow", "feed"}
                      {"action": "play", "feed", "url"}   (as POST /music's gate: saved, then played)
"""

from __future__ import annotations

import sys
import urllib.parse
from http.server import BaseHTTPRequestHandler

from . import auth

PATHS = frozenset({"/podcasts/discover", "/podcasts/show"})


def _q(h: BaseHTTPRequestHandler, name: str) -> str:
    return (urllib.parse.parse_qs(h.path.partition("?")[2]).get(name) or [""])[0].strip()


def handle(h: BaseHTTPRequestHandler, method: str, path: str, bearer: str, send_json, read_json) -> bool:
    from agent_media_core import podcast_discover as P

    if method not in ("GET", "POST") or (path == "/podcasts/discover" and method != "GET"):
        return False
    user, err = auth.gate(bearer)
    if not user:
        send_json(h, err.pop("status", 401), {"ok": False, **err})
        return True
    try:
        if path == "/podcasts/discover":
            page = _q(h, "page") or "1"
            if not page.isdigit():
                raise ValueError("page is a number")
            send_json(h, 200, {"ok": True, **P.listing(_q(h, "list") or "top", _q(h, "q"), _q(h, "genre"), int(page))})
        elif method == "GET":
            page = _q(h, "page") or "1"
            if not page.isdigit():
                raise ValueError("page is a number")
            send_json(h, 200, {"ok": True, **P.show(_q(h, "itunes"), _q(h, "feed"), _q(h, "q"), int(page))})
        else:
            body = read_json(h)
            body = body if isinstance(body, dict) else {}
            action = str(body.get("action") or "")
            if action == "follow":
                P.follow(body.get("feed") or "", body.get("title") or "", body.get("author") or "",
                         body.get("art") or "", str(body.get("itunes") or ""))
                send_json(h, 200, {"ok": True, "following": True})
            elif action == "unfollow":
                P.unfollow(body.get("feed") or "")
                send_json(h, 200, {"ok": True, "following": False})
            elif action == "play":
                got = P.episode(body.get("feed") or "", str(body.get("url") or ""))
                from . import music
                ok, out = music.control({"action": "book", "uri": got["uri"]}, bearer)
                send_json(h, 200 if ok else out.pop("status", 502), {"ok": ok, **out, "episode": got})
            else:
                raise ValueError("action must be follow, unfollow or play")
    except ValueError as e:
        send_json(h, 400, {"ok": False, "error": str(e)})
    except LookupError as e:
        send_json(h, 404, {"ok": False, "error": str(e)})
    except Exception as e:  # noqa: BLE001
        print(f"podcasts: {e}", file=sys.stderr)
        send_json(h, 502, {"ok": False, "error": f"couldn't reach the podcast directory: {e}"})
    return True
