"""`POST /link/preview {url}` — a title (and picture) for a video link's card.

David, 26 Oct 2026: "let's include YouTube", "let's include Facebook too".
The app shows a YouTube or Facebook video link in a message as a card that
plays in its viewer. A card wants a title, and a Facebook short link
(fb.watch, /share/v/, /share/r/) wants resolving to the video it points at,
which the embed player needs. The phone loads nothing from either site until
a card is tapped, so this asks from the server instead: through read_it's
safe fetch (public addresses only, at every redirect), YouTube's oEmbed for a
title, a Facebook page's og:title, og:image and og:url (asked as Facebook's
own link previewer, which public pages answer with their tags).

Only YouTube and Facebook links; anything else is a 400. Answers are kept a
day (CACHE_S), the newest KEEP. A fetch that fails answers what is known —
the link itself, no title — so a card always draws.
"""

from __future__ import annotations

import html
import json
import re
import threading
import time
from urllib.parse import quote, urlparse

from . import auth

CACHE_S = 24 * 3600
KEEP = 500
FB_UA = "facebookexternalhit/1.1 (+http://www.facebook.com/externalhit_uatext.php)"
_CACHE: dict[str, tuple[float, dict]] = {}
_LOCK = threading.Lock()

_YT_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com", "youtu.be"}
_FB_HOSTS = {"facebook.com", "www.facebook.com", "m.facebook.com", "web.facebook.com", "fb.watch"}


def kind_of(url: str) -> str:
    """'youtube', 'facebook', or '' for a link this route does not preview."""
    try:
        host = (urlparse(url).hostname or "").lower()
    except ValueError:
        return ""
    if urlparse(url).scheme not in ("http", "https"):
        return ""
    return "youtube" if host in _YT_HOSTS else "facebook" if host in _FB_HOSTS else ""


def _meta(page: str, prop: str) -> str:
    for pat in (rf'<meta[^>]+(?:property|name)=["\']{re.escape(prop)}["\'][^>]*content=["\']([^"\']*)["\']',
                rf'<meta[^>]+content=["\']([^"\']*)["\'][^>]*(?:property|name)=["\']{re.escape(prop)}["\']'):
        m = re.search(pat, page, re.I)
        if m:
            return html.unescape(m.group(1)).strip()
    return ""


def _fetch(url: str, ua: str) -> tuple[str, str, str]:
    """read_it's safe fetch (public addresses only, at every redirect; its deadline)."""
    from . import read_it

    return read_it._get(url, ua)


def _youtube(url: str) -> dict:
    final, _ctype, body = _fetch(f"https://www.youtube.com/oembed?format=json&url={quote(url, safe='')}",
                                 "Sasonica/1.0 (a link card; https://sasonica.com)")
    got = json.loads(body)
    return {"url": url, "title": str(got.get("title") or "")[:200],
            "image": str(got.get("thumbnail_url") or ""), "site": "YouTube"}


def _facebook(url: str) -> dict:
    final, _ctype, page = _fetch(url, FB_UA)
    canonical = _meta(page, "og:url") or final
    if kind_of(canonical) != "facebook":
        canonical = final
    return {"url": canonical, "title": _meta(page, "og:title")[:200],
            "image": _meta(page, "og:image"), "site": "Facebook"}


def preview(url: str, now: float | None = None) -> dict:
    """`{url, title, image, site}` for a YouTube or Facebook link; never raises
    for a fetch that fails (the link, no title)."""
    now = time.time() if now is None else now
    kind = kind_of(url)
    with _LOCK:
        hit = _CACHE.get(url)
        if hit and now - hit[0] < CACHE_S:
            return hit[1]
    try:
        got = _youtube(url) if kind == "youtube" else _facebook(url)
    except Exception:  # noqa: BLE001 — a card without a title, not no card
        got = {"url": url, "title": "", "image": "", "site": "YouTube" if kind == "youtube" else "Facebook"}
    with _LOCK:
        _CACHE[url] = (now, got)
        if len(_CACHE) > KEEP:
            for k in sorted(_CACHE, key=lambda k: _CACHE[k][0])[: len(_CACHE) - KEEP]:
                del _CACHE[k]
    return got


def post(body, bearer: str) -> tuple[bool, dict]:
    """POST /link/preview {"url"}."""
    user, err = auth.gate(bearer)
    if not user:
        return False, err
    url = str((body or {}).get("url") or "").strip() if isinstance(body, dict) else ""
    if not url or len(url) > 2000 or not kind_of(url):
        return False, {"error": "a YouTube or Facebook link only", "status": 400}
    return True, preview(url)


def _reset_for_tests() -> None:
    with _LOCK:
        _CACHE.clear()
