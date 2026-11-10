"""Practices that play in their maker's own embedded player, listed beside the
rest (David, 1 Nov 2026: saved Insight Timer tracks, "can we still index
them"). The index is the person's own data, so it is never in this package:
MEDIA_PRACTICE_EMBED_INDEX names a JSON list of
``{"title", "teacher", "length"?, "folders"?, "embed", "page"?, "kind"?}``
(an Insight Timer export: ``insight-timer/saved-index.json``). Unset, there
are none.

Each becomes an item ``it:<hash>`` with source ``embed``, its ``embed`` and
``page`` links (public pages, unlike the rest of the list's addresses, since
the app plays them itself) and ``folders``. Playing one plays nothing here:
the play is counted and the app is told the embed to open (practice_library
.play, server-contract.md §6.45).

**Pictures** (David, 10 Nov 2026: "let's get the pictures"). The export has
none, so each embed's own page is read once for its ``og:image`` (an Insight
Timer track's ``…/pictures/tiny_rectangle_medium.jpeg``) by `fill_art`, in
the background, one page a second, and kept in
``<state_dir>/practice-embed-art.json`` (``{embed: {"art", "at"}}``; a page
with none is asked again after a week). An item then carries ``art`` (the
small one, for its row) and ``art_large`` (Insight Timer's
``rectangle_medium``, for its info page). MEDIA_PRACTICE_EMBED_ART=0: none
are asked for.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
import urllib.request
from pathlib import Path
from typing import Optional

from ._paths import state_dir


def index_path() -> Optional[Path]:
    raw = (os.environ.get("MEDIA_PRACTICE_EMBED_INDEX") or "").strip()
    return Path(raw).expanduser() if raw else None


def _seconds(length: str) -> Optional[int]:
    """"12 min", "1 hr 1 min" → seconds."""
    m = re.fullmatch(r"\s*(?:(\d+)\s*hr?s?)?\s*(?:(\d+)\s*min)?\s*", length or "")
    if not m or not (m.group(1) or m.group(2)):
        return None
    return (int(m.group(1) or 0) * 60 + int(m.group(2) or 0)) * 60 or None


def items() -> list[dict]:
    p = index_path()
    if p is None:
        return []
    try:
        rows = json.loads(p.read_text())
    except (OSError, ValueError):
        return []
    out = []
    art = _art_cache()
    for r in rows if isinstance(rows, list) else []:
        embed = r.get("embed") if isinstance(r, dict) else None
        if not (isinstance(embed, str) and embed.startswith("https://")):
            continue
        title = (r.get("title") or "").strip() or embed
        teacher = (r.get("teacher") or "").strip()
        secs = _seconds(r.get("length") or "")
        out.append({"id": "it:" + hashlib.sha256(embed.encode()).hexdigest()[:12],
                    "title": f"{title} — {teacher}" if teacher else title,
                    "source": "embed", "embed": embed, "page": r.get("page") or None,
                    "folders": list(r.get("folders") or []),
                    "duration": str(secs) if secs else "", "_uri": embed,
                    **_art_fields(art.get(embed))})
    return out


# ---- pictures ------------------------------------------------------------------

#: A page that had no picture is asked again after this long.
RETRY_S = 7 * 86400
#: One page at a time, this far apart: 1,265 tracks take about 20 minutes once.
GAP_S = 1.0
_MAX_PAGE = 768 * 1024
_UA = "Mozilla/5.0 (compatible; agent-media practice pictures)"
_IMAGE = re.compile(r'<meta[^>]+(?:property|name)=["\']og:image["\'][^>]*content=["\'](https://[^"\']+)["\']'
                    r'|<meta[^>]+content=["\'](https://[^"\']+)["\'][^>]*(?:property|name)=["\']og:image["\']', re.I)
_PICTURE = re.compile(r'https://libraryitems\.insighttimer\.com/[A-Za-z0-9/_.-]+/pictures/[a-z_]+\.(?:jpe?g|png|webp)')
_lock = threading.Lock()
_running = False


def art_path() -> Path:
    return state_dir() / "practice-embed-art.json"


def _art_cache() -> dict:
    try:
        d = json.loads(art_path().read_text())
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _art_fields(hit) -> dict:
    url = hit.get("art") if isinstance(hit, dict) else ""
    if not (isinstance(url, str) and url.startswith("https://")):
        return {}
    large = re.sub(r"/pictures/tiny_rectangle_medium\.(jpe?g)$", r"/pictures/rectangle_medium.\1", url)
    return {"art": url, "art_large": large}


def picture_in(page: str) -> str:
    """The picture a page names: its og:image, else an Insight Timer picture
    URL anywhere in it (the widget page has no og tags); "" for none. A
    square og:image is swapped for the rectangle beside it, which fits a row."""
    m = _IMAGE.search(page)
    url = (m.group(1) or m.group(2)) if m else ""
    if not url:
        m = _PICTURE.search(page)
        url = m.group(0) if m else ""
    return re.sub(r"/pictures/tiny_square_medium\.(jpe?g)$", r"/pictures/tiny_rectangle_medium.\1", url)


def _fetch_page(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    with urllib.request.urlopen(req, timeout=15) as r:
        return r.read(_MAX_PAGE).decode("utf-8", "replace")


def _wanted(now: float) -> list[str]:
    """Embeds with no picture known, and not asked within RETRY_S."""
    cache = _art_cache()
    out = []
    for i in items():
        hit = cache.get(i["embed"])
        if isinstance(hit, dict) and (hit.get("art") or now - float(hit.get("at") or 0) < RETRY_S):
            continue
        out.append(i["embed"])
    return out


def fill_art(limit: Optional[int] = None, gap: float = GAP_S) -> int:
    """Ask each embed's page for its picture, keeping each answer as it comes;
    the number found. Off with MEDIA_PRACTICE_EMBED_ART=0."""
    if (os.environ.get("MEDIA_PRACTICE_EMBED_ART") or "1").strip() == "0":
        return 0
    found = 0
    for n, embed in enumerate(_wanted(time.time())[:limit]):
        if n and gap:
            time.sleep(gap)
        try:
            art = picture_in(_fetch_page(embed))
        except Exception:  # noqa: BLE001 — that one waits a week; the rest go on
            art = ""
        cache = _art_cache()
        cache[embed] = {"art": art, "at": time.time()}
        p = art_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(cache))
        tmp.replace(p)
        found += bool(art)
    return found


def fill_art_soon() -> bool:
    """`fill_art` on a thread of its own, unless one is running or nothing is
    wanted (GET /practice calls this after answering). True when started."""
    global _running
    if (os.environ.get("MEDIA_PRACTICE_EMBED_ART") or "1").strip() == "0":
        return False
    with _lock:
        if _running or not _wanted(time.time()):
            return False
        _running = True

    def run():
        global _running
        try:
            fill_art()
        finally:
            with _lock:
                _running = False

    threading.Thread(target=run, name="practice-embed-art", daemon=True).start()
    return True
