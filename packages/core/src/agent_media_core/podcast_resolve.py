"""A podcast episode's audio from a link shared out of a podcast app.

David, 1 Nov 2026: "I tried to share a podcast with sasonica but it came
through as music. I didn't seem to be able to get past the first step with
the podcast option" — shared from the PodcastOne Australia app, whose page
this host may not read (403), and which neither Play it nor the Podcasts
tab knew: Play it took the link for music, the tab took YouTube only.

`episode(url, text)` tries, in turn:

1. the link itself, when it is an audio file;
2. Apple Podcasts' own episode id (``podcasts.apple.com/…?i=<id>``), looked
   up in the iTunes directory;
3. the page: its ``og:audio``, an ``<audio>``/``<source>`` or an RSS
   ``<enclosure>``, and its ``og:title`` for the search below;
4. the iTunes podcast directory searched with the words shared beside the
   link (a podcast app shares "<episode> — <show> <link>") or the page's
   title, the episode whose title shares the most words with them, and only
   one that shares at least half of its own.

It answers ``{"audio", "title", "show", "art", "published", "duration_s",
"via"}`` or None. Network only, nothing played or saved here.
MEDIA_PODCAST_RESOLVE=0 turns it off (pinned off in the tests' conftests).
"""

from __future__ import annotations

import html
import json
import re
import urllib.parse
import urllib.request
from datetime import datetime
from typing import Optional

AUDIO_EXTS = (".mp3", ".m4a", ".aac", ".ogg", ".opus", ".wav", ".flac")
ITUNES_SEARCH = "https://itunes.apple.com/search"
ITUNES_LOOKUP = "https://itunes.apple.com/lookup"
_UA = "Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130 Mobile Safari/537.36"
_URL = re.compile(r"https?://\S+")
_WORD = re.compile(r"[a-z0-9]+")
#: Words a share wraps an episode in, never part of its title.
_FILLER = {"listen", "to", "this", "episode", "check", "out", "on", "from", "via", "podcast",
           "podcastone", "australia", "shared", "the", "a", "an", "and", "of", "with", "in", "app"}


def _get(url: str, timeout: float = 10.0) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": _UA, "Accept-Language": "en-AU,en"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read(2_000_000)


def _is_audio(url: str) -> bool:
    return urllib.parse.urlparse(url).path.lower().endswith(AUDIO_EXTS)


def _words(text: str) -> list[str]:
    return [w for w in _WORD.findall(html.unescape(text or "").lower()) if w not in _FILLER]


def _from_itunes(r: dict, via: str) -> Optional[dict]:
    audio = r.get("episodeUrl") or ""
    if not audio.startswith(("http://", "https://")):
        return None
    published = None
    try:
        published = datetime.fromisoformat(str(r.get("releaseDate")).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        pass
    ms = r.get("trackTimeMillis")
    return {"audio": audio, "title": r.get("trackName") or None, "show": r.get("collectionName") or None,
            "art": r.get("artworkUrl600") or r.get("artworkUrl160") or None, "published": published,
            "duration_s": int(ms / 1000) if isinstance(ms, (int, float)) and ms else None, "via": via}


def _apple(url: str) -> Optional[dict]:
    """An Apple Podcasts link with its episode id (``?i=``)."""
    u = urllib.parse.urlparse(url)
    if not u.netloc.endswith("podcasts.apple.com"):
        return None
    ep = (urllib.parse.parse_qs(u.query).get("i") or [""])[0]
    show = re.search(r"/id(\d+)", u.path)
    if not (ep and show):
        return None
    got = json.loads(_get(f"{ITUNES_LOOKUP}?id={show.group(1)}&entity=podcastEpisode&limit=300"))
    r = next((x for x in got.get("results", []) if str(x.get("trackId")) == ep), None)
    return _from_itunes(r, "apple") if r else None


def _page(url: str) -> tuple[Optional[str], str]:
    """The audio a page names, and its title; (None, "") when it will not
    be read (a sign-in page, a site that refuses this host)."""
    try:
        doc = _get(url).decode("utf-8", "replace")
    except Exception:  # noqa: BLE001 — a page that will not be read
        return None, ""

    def meta(prop: str) -> str:
        m = re.search(rf'<meta[^>]+(?:property|name)=["\']{re.escape(prop)}["\'][^>]*content=["\']([^"\']+)', doc, re.I) \
            or re.search(rf'<meta[^>]+content=["\']([^"\']+)["\'][^>]*(?:property|name)=["\']{re.escape(prop)}["\']', doc, re.I)
        return html.unescape(m.group(1)).strip() if m else ""

    title = meta("og:title") or meta("twitter:title")
    for cand in (meta("og:audio"), meta("og:audio:url"), meta("og:audio:secure_url"), meta("twitter:player:stream")):
        if cand.startswith(("http://", "https://")):
            return cand, title
    for rx in (r'<(?:audio|source)[^>]+src=["\']([^"\']+)', r'<enclosure[^>]+url=["\']([^"\']+)',
               r'"contentUrl"\s*:\s*"([^"]+)"'):
        for m in re.finditer(rx, doc, re.I):
            cand = urllib.parse.urljoin(url, html.unescape(m.group(1)))
            if _is_audio(cand):
                return cand, title
    return None, title


def _search(text: str) -> Optional[dict]:
    """The directory's episode best matching `text`, if it matches well."""
    words = _words(text)
    if len(words) < 2:
        return None
    q = urllib.parse.urlencode({"term": " ".join(words[:12]), "entity": "podcastEpisode", "limit": 25, "country": "AU"})
    got = json.loads(_get(f"{ITUNES_SEARCH}?{q}"))
    want = set(words)
    best, score = None, 0.0
    for r in got.get("results", []):
        own = set(_words(r.get("trackName") or ""))
        if not own:
            continue
        shared = len(own & want)
        # Most of the episode's own title among the words shared, with its
        # show's name as a tie-break.
        s = shared / len(own) + 0.1 * len(set(_words(r.get("collectionName") or "")) & want)
        if shared / len(own) >= 0.5 and s > score:
            best, score = r, s
    return _from_itunes(best, "search") if best else None


def looks_like_podcast(url: str) -> bool:
    """A link a podcast app would share: a known podcast site, or one whose
    address says podcast or episode."""
    from .share import PODCAST_HOSTS, _host_matches
    u = urllib.parse.urlparse(url or "")
    host = u.netloc.lower()
    return _host_matches(host, PODCAST_HOSTS) or "podcast" in host or bool(re.search(r"podcast|episode", u.path, re.I))


def episode(url: str, text: str = "", search: bool = True) -> Optional[dict]:
    """The episode `url` (and the words shared with it) is, as its audio
    and what is known about it; None when it cannot be found. Without
    `search`, the directory is not searched by words (a link that need not
    be a podcast at all)."""
    import os
    url = (url or "").strip()
    words = _URL.sub(" ", text or "").strip()
    if not url.startswith(("http://", "https://")):
        return None
    if os.environ.get("MEDIA_PODCAST_RESOLVE") == "0":
        return None
    if _is_audio(url):
        return {"audio": url, "title": words or None, "show": None, "art": None,
                "published": None, "duration_s": None, "via": "link"}
    try:
        got = _apple(url)
        if got:
            return got
    except Exception:  # noqa: BLE001 — try the page and the search
        pass
    audio, title = _page(url)
    if audio:
        return {"audio": audio, "title": title or words or None, "show": None, "art": None,
                "published": None, "duration_s": None, "via": "page"}
    for q in ((words, title, f"{words} {title}") if search else ()):
        try:
            got = _search(q)
        except Exception:  # noqa: BLE001 — the directory not answering
            got = None
        if got:
            return got
    return None
