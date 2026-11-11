"""Practices to find on YouTube, beyond those saved (server-contract.md §6.45
"Search and browse"; David, 11 Nov 2026: "Can we also do search and browse
for everything?").

  search(q)    the host's YouTube search for the words (music_discover's:
               Mopidy's YouTube results, else the DJ's), kept an hour
  style(name)  the same for one of the style tree's kinds ("Yin yoga",
               "Body scan", "Breathwork", …: STYLES), kept a day

Each result `{id: "yt:<vid>", youtube_id, title, channel, duration_s, art,
saved}`; `saved` when the Practice list has it. Saving is POST /practice
{"action": "add", "url"}, as a pasted link.
"""

from __future__ import annotations

import time
from typing import Optional

STYLE_TTL_S = 86400.0


def styles() -> list[str]:
    """The style tree's kinds as searchable names, in the tree's order:
    a leaf with its parent when it needs it ("Yin yoga"), else itself."""
    from . import practice_library as lib
    out: list[str] = []
    for path, _rule in lib.STYLE_RULES:
        leaf = path[-1]
        if leaf in ("Music", "Talks", "For sleep"):
            continue
        name = f"{leaf} {path[-2].lower()}" if len(path) >= 3 and path[-2] not in lib.STYLE_TOP else leaf
        if name not in out:
            out.append(name)
    return out


def _rows(items: list[dict]) -> list[dict]:
    from . import practice_library as lib
    try:
        saved = {i["id"] for i in lib.items() if str(i.get("id") or "").startswith("yt:")}
    except Exception:  # noqa: BLE001 — none marked, not no results
        saved = set()
    out = []
    for i in items:
        vid = str(i.get("uri") or "")[3:]
        if not vid:
            continue
        out.append({"id": f"yt:{vid}", "youtube_id": vid, "title": i.get("title") or "",
                    "channel": i.get("artist") or None, "duration_s": i.get("duration_s"),
                    "art": f"https://i.ytimg.com/vi/{vid}/mqdefault.jpg", "saved": f"yt:{vid}" in saved})
    return out


def search(q: str, now: Optional[float] = None) -> list[dict]:
    from . import music_discover
    q = " ".join(str(q or "").split())[:200]
    if len(q) < 2:
        raise ValueError("search needs q, two letters or more")
    return _rows(music_discover._search(q, time.time() if now is None else now))


def style(name: str, now: Optional[float] = None) -> list[dict]:
    from . import music_discover
    if name not in styles():
        raise ValueError("style is one of the styles listed")
    now = time.time() if now is None else now
    got = music_discover._cached(f"practice-style:{name}", STYLE_TTL_S,
                                 lambda: music_discover._search(f"{name} practice", now), now)
    return _rows(got)
