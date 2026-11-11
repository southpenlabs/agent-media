"""One search across the Media tabs (server-contract.md §6.46a; David, 11
Nov 2026: "Can we also do search and browse for everything?").

  GET /media/search?q=<words>   gated → {"ok", "q", "groups": {kind: {"mine": [...], "found": [...]}}}

For each tab, what is yours that matches (what was played, the book library,
the saved practices, what Trakt says you watched) and what the outside
source finds (the host's YouTube search, the book library, Apple Podcasts,
Trakt), at most LIMIT of each, asked side by side; a part that fails or is
not set up is left out (its group empty or absent).
"""

from __future__ import annotations

import sys
from concurrent.futures import ThreadPoolExecutor

from . import auth

LIMIT = 5
WAIT_S = 20.0


def _music_mine(q):
    from agent_media_core import music_recent
    return music_recent.listed(kind="music", which="recent", q=q)[:LIMIT]


def _music_found(q):
    import time

    from agent_media_core import music_discover
    return music_discover._search(q, time.time())[:LIMIT]


def _book_mine(q):
    from agent_media_core import music_recent
    played = music_recent.listed(kind="book", which="recent", q=q)
    seen = {r.get("uri") for r in played}
    library = [r for r in music_recent.listed(kind="book", which="all", q=q) if r.get("uri") not in seen]
    return (played + library)[:LIMIT]


def _podcast_mine(q):
    from agent_media_core import music_recent
    return music_recent.listed(kind="podcast", which="recent", q=q)[:LIMIT]


def _podcast_found(q):
    import time

    from agent_media_core import podcast_discover
    return podcast_discover.search(q, time.time())[:LIMIT]


def _practice_mine(q):
    from agent_media_core import music_recent, practice_library
    return [i for i in practice_library.items() if music_recent.matches(
        {"title": i.get("title"), "author": i.get("teacher"), "genre": i.get("style")}, q)][:LIMIT]


def _practice_found(q):
    from agent_media_core import practice_discover
    return practice_discover.search(q)[:LIMIT]


def _watch_mine(kind):
    def run(q):
        from agent_media_core import music_recent, trakt
        if not trakt.signed_in():
            return []
        rows = trakt.listing(kind, "recent")["items"] + trakt.listing(kind, "watchlist")["items"]
        out, seen = [], set()
        for r in rows:
            key = r.get("show") or r.get("title")
            if key in seen or not music_recent.matches({"title": r.get("title"), "artist": r.get("show")}, q):
                continue
            seen.add(key)
            out.append(r)
        return out[:LIMIT]
    return run


def _watch_found(kind):
    def run(q):
        from agent_media_core import trakt
        return trakt.search(kind, q)[:LIMIT] if trakt.signed_in() else []
    return run


PARTS = {
    "music": (_music_mine, _music_found),
    "book": (_book_mine, None),
    "podcast": (_podcast_mine, _podcast_found),
    "practice": (_practice_mine, _practice_found),
    "movie": (_watch_mine("movie"), _watch_found("movie")),
    "tv": (_watch_mine("tv"), _watch_found("tv")),
}


def search(q: str, bearer: str) -> tuple[bool, dict]:
    user, err = auth.gate(bearer)
    if not user:
        return False, err
    q = " ".join(str(q or "").split())[:200]
    if len(q) < 2:
        return False, {"error": "q needs two letters or more", "status": 400}
    jobs = {}
    with ThreadPoolExecutor(max_workers=8) as pool:
        for kind, (mine, found) in PARTS.items():
            jobs[(kind, "mine")] = pool.submit(mine, q)
            if found:
                jobs[(kind, "found")] = pool.submit(found, q)
        groups: dict = {k: {"mine": [], "found": []} for k in PARTS}
        for (kind, part), fut in jobs.items():
            try:
                groups[kind][part] = fut.result(timeout=WAIT_S) or []
            except Exception as e:  # noqa: BLE001 — that part left empty
                print(f"media search: {kind} {part}: {e}", file=sys.stderr)
    return True, {"q": q, "groups": groups}
