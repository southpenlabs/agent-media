"""The Videos tab: one search across several video sites (server-contract
§6.70; agent_media_core.videos; docs/proposals/2026-11-25-videos-tab.md).

  GET /videos/sources   gated → {"ok", "sources": [{id, name, site, filters, types, lists}],
                                 "filters": {length, date, type, sort}}
  GET /videos/search?q=&sources=yt,peertube,archive,dailymotion,odysee,vimeo&length=&date=&type=&sort=&page=
                        gated → {"ok", "q", "page", "filters", "items": [VideoItem],
                                 "sources": [{id, ok, error?, count?, next?, ignored?}]}
"""

from __future__ import annotations

import urllib.parse
from http.server import BaseHTTPRequestHandler

from . import auth

PATHS = frozenset({"/videos/sources", "/videos/search"})


def handle(h: BaseHTTPRequestHandler, method: str, path: str, bearer: str, send_json) -> bool:
    from agent_media_core import videos

    user, err = auth.gate(bearer)
    if not user:
        send_json(h, err.pop("status", 401), {"ok": False, **err})
        return True
    if method != "GET":
        send_json(h, 405, {"ok": False, "error": "GET only"})
        return True
    if path == "/videos/sources":
        send_json(h, 200, {"ok": True, "sources": videos.sources(),
                           "filters": {k: list(v) for k, v in videos.FILTERS.items()}})
        return True
    q = {k: (v or [""])[0] for k, v in urllib.parse.parse_qs(h.path.partition("?")[2]).items()}
    try:
        filters = videos.check(q)
        wanted = [s.strip() for s in (q.get("sources") or "").split(",") if s.strip()] or None
        try:
            page = int(q.get("page") or 1)
        except ValueError:
            raise ValueError("page is a number from 1") from None
        got = videos.search(q.get("q") or "", wanted, filters, page)
    except ValueError as e:
        send_json(h, 400, {"ok": False, "error": str(e)})
        return True
    send_json(h, 200, {"ok": True, **got})
    return True
