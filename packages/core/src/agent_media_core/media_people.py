"""Who made what is in the Media tab: a music artist, a book's author, a
podcast's show — a profile page for each, as Practice's teachers have.

David, 1 Nov 2026: "Maybe we could do the same with the other tabs? as in
artist, author, podcaster" … "yeah let's do the other profiles too".

`profile(kind, name)` answers ``{"name", "kind", "profile": {...} | None}``
with ``{"source", "name", "photo", "description", "region", "facts":
[...], "links": [{"label", "url"}]}``, looked up once and kept in
``<state_dir>/media-people.json`` (a month; one not found asked again
after a week):

- **artist** — MusicBrainz (the central layer for music, per
  docs/proposals/2026-11-01-three-data-layers.md): the artist whose name
  matches exactly, its area, type and dates, its links; the photo and the
  words from the English Wikipedia page MusicBrainz links (through
  Wikidata), so the page is that artist's and no namesake's.
- **author** — the English Wikipedia page of that name, only when its
  description says a writer; else the description Audiobookshelf keeps
  for the author.
- **show** — the iTunes podcast directory's show of that name: artwork,
  who makes it, genre, episode count, its Apple page; the words from the
  show's own feed.

Each install fetches for its own user (the proposal: profile text is not
copied into central). MEDIA_PEOPLE=0 turns the lookups off (pinned off in
the tests' conftests).
"""

from __future__ import annotations

import html
import json
import os
import re
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Optional

from ._paths import state_dir

KINDS = ("artist", "author", "show")
TTL_S = 30 * 86400
MISS_TTL_S = 7 * 86400
_UA = "agent-media/1.0 ( https://sasonica.com )"
MB = "https://musicbrainz.org/ws/2"
WIKI_SUMMARY = "https://en.wikipedia.org/api/rest_v1/page/summary/"
WIKIDATA = "https://www.wikidata.org/wiki/Special:EntityData/{id}.json"
ITUNES_SEARCH = "https://itunes.apple.com/search"
_WRITER = re.compile(r"writer|author|novelist|poet|journalist|essayist|screenwriter|playwright|historian|"
                     r"philosopher|psychologist|teacher|scientist|monk|lama|journalist", re.I)


def _path() -> Path:
    return state_dir() / "media-people.json"


def _norm(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (name or "").lower())


def _get(url: str, timeout: float = 12.0) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": _UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read(3_000_000)


def _json(url: str):
    return json.loads(_get(url))


def _wiki(title: str) -> Optional[dict]:
    """English Wikipedia's summary of `title`: words, picture, page."""
    try:
        d = _json(WIKI_SUMMARY + urllib.parse.quote(title.replace(" ", "_")))
    except Exception:  # noqa: BLE001 — no page
        return None
    if d.get("type") == "disambiguation" or not d.get("extract"):
        return None
    return {"description": d.get("extract"), "short": d.get("description") or "",
            "photo": (d.get("thumbnail") or {}).get("source"),
            "url": ((d.get("content_urls") or {}).get("desktop") or {}).get("page")}


# ---- artists ------------------------------------------------------------------------

def _artist(name: str) -> Optional[dict]:
    q = urllib.parse.urlencode({"query": f'artist:"{name}"', "fmt": "json", "limit": 10})
    hits = _json(f"{MB}/artist?{q}").get("artists") or []
    hit = next((a for a in hits if _norm(a.get("name", "")) == _norm(name)), None)
    if not hit:
        return None
    time.sleep(1.0)  # MusicBrainz asks for one request a second
    a = _json(f"{MB}/artist/{hit['id']}?inc=url-rels&fmt=json")
    links, wiki = [], None
    for rel in a.get("relations") or []:
        url = ((rel.get("url") or {}).get("resource")) or ""
        kind = rel.get("type") or ""
        if kind == "official homepage":
            links.append({"label": "Website", "url": url})
        elif kind == "wikidata" and not wiki:
            qid = url.rstrip("/").rsplit("/", 1)[-1]
            try:
                ent = _json(WIKIDATA.format(id=qid))["entities"][qid]
                title = ((ent.get("sitelinks") or {}).get("enwiki") or {}).get("title")
                wiki = _wiki(title) if title else None
            except Exception:  # noqa: BLE001 — without its words
                wiki = None
        elif kind == "bandcamp":
            links.append({"label": "Bandcamp", "url": url})
    span = a.get("life-span") or {}
    begin, end = (span.get("begin") or "")[:4], (span.get("end") or "")[:4]
    group = (a.get("type") or "") in ("Group", "Orchestra", "Choir")
    facts = []
    if begin:
        facts.append((f"formed {begin}" if group else f"born {begin}") + (f", ended {end}" if end and group else
                                                                            f", died {end}" if end else ""))
    if a.get("type") and a["type"] not in ("Person",):
        facts.append(a["type"].lower())
    area = (a.get("area") or {}).get("name")
    begin_area = (a.get("begin-area") or {}).get("name")
    region = ", ".join(x for x in (begin_area if begin_area != area else None, area) if x) or None
    if wiki and wiki.get("url"):
        links.append({"label": "Wikipedia", "url": wiki["url"]})
    links.append({"label": "MusicBrainz", "url": f"https://musicbrainz.org/artist/{a['id']}"})
    seen: set = set()
    links = [x for x in links if not (x["label"] in seen or seen.add(x["label"]))]
    return {"source": "musicbrainz", "name": a.get("name") or name, "photo": (wiki or {}).get("photo"),
            "description": (wiki or {}).get("description"), "region": region, "facts": facts, "links": links}


# ---- authors ------------------------------------------------------------------------

def _abs_author(name: str) -> Optional[str]:
    """Audiobookshelf's description of the author, where it keeps one."""
    try:
        from . import book_tracks
        ready = book_tracks._abs_ready()
        if not ready:
            return None
        url, token, libs = ready
        for lib in libs:
            req = urllib.request.Request(f"{url}/api/libraries/{lib['id']}/authors",
                                         headers={"Authorization": f"Bearer {token}"})
            with urllib.request.urlopen(req, timeout=12) as r:
                for a in json.loads(r.read()).get("authors") or []:
                    if _norm(a.get("name", "")) == _norm(name) and (a.get("description") or "").strip():
                        return a["description"].strip()
    except Exception:  # noqa: BLE001 — none kept
        return None
    return None


def _author(name: str) -> Optional[dict]:
    wiki = _wiki(name)
    if wiki and not _WRITER.search(wiki.get("short") or ""):
        wiki = None
    words = (wiki or {}).get("description") or _abs_author(name)
    if not (wiki or words):
        return None
    links = [{"label": "Wikipedia", "url": wiki["url"]}] if wiki and wiki.get("url") else []
    return {"source": "wikipedia" if wiki else "audiobookshelf", "name": name, "photo": (wiki or {}).get("photo"),
            "description": words, "region": None,
            "facts": [wiki["short"]] if wiki and wiki.get("short") else [], "links": links}


# ---- shows --------------------------------------------------------------------------

def _feed_words(feed: str) -> Optional[str]:
    try:
        root = ET.fromstring(_get(feed))
    except Exception:  # noqa: BLE001 — without its words
        return None
    ch = root.find("channel")
    if ch is None:
        return None
    for tag in ("description", "{http://www.itunes.com/dtds/podcast-1.0.dtd}summary"):
        el = ch.find(tag)
        if el is not None and (el.text or "").strip():
            text = re.sub(r"<[^>]+>", " ", html.unescape(el.text))
            return re.sub(r"\s+", " ", text).strip()[:2000] or None
    return None


def _show(name: str) -> Optional[dict]:
    q = urllib.parse.urlencode({"term": name, "entity": "podcast", "limit": 10, "country": "AU"})
    hits = _json(f"{ITUNES_SEARCH}?{q}").get("results") or []
    hit = next((r for r in hits if _norm(r.get("collectionName", "")) == _norm(name)), None)
    if not hit:
        return None
    facts = [x for x in (f"by {hit['artistName']}" if hit.get("artistName") else "",
                         hit.get("primaryGenreName") or "",
                         f"{hit['trackCount']} episodes" if hit.get("trackCount") else "") if x]
    links = [{"label": "Apple Podcasts", "url": hit["collectionViewUrl"]}] if hit.get("collectionViewUrl") else []
    return {"source": "itunes", "name": hit.get("collectionName") or name,
            "photo": hit.get("artworkUrl600") or hit.get("artworkUrl100"),
            "description": _feed_words(hit["feedUrl"]) if hit.get("feedUrl") else None,
            "region": None, "facts": facts, "links": links}


# ---- the profile --------------------------------------------------------------------

def profile(kind: str, name: str, fresh: bool = False) -> dict:
    """Who `name` is, as a `kind` (artist, author, show); see the module."""
    name = (name or "").strip()
    if kind not in KINDS or not name:
        return {"name": name, "kind": kind, "profile": None}
    try:
        kept = json.loads(_path().read_text())
    except (OSError, ValueError):
        kept = {}
    key = f"{kind}:{_norm(name)}"
    hit = kept.get(key) if isinstance(kept.get(key), dict) else None
    ttl = TTL_S if hit and hit.get("profile") else MISS_TTL_S
    if hit and not fresh and time.time() - float(hit.get("at") or 0) < ttl:
        return {"name": name, "kind": kind, "profile": hit.get("profile")}
    if os.environ.get("MEDIA_PEOPLE") == "0":
        return {"name": name, "kind": kind, "profile": (hit or {}).get("profile")}
    try:
        got = {"artist": _artist, "author": _author, "show": _show}[kind](name)
    except Exception:  # noqa: BLE001 — the last one known, or none
        return {"name": name, "kind": kind, "profile": (hit or {}).get("profile")}
    kept[key] = {"profile": got, "at": time.time()}
    p = _path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(f".tmp.{os.getpid()}")
    tmp.write_text(json.dumps(kept))
    tmp.replace(p)
    return {"name": name, "kind": kind, "profile": got}
