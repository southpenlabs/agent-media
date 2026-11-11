"""MusicBrainz for the Music tab: artists, albums and their songs to browse
(server-contract.md §6.9a "MusicBrainz"; David, 11 Nov 2026, yes to
"MusicBrainz browsing for Music").

  search(q)         artists and albums (release groups) by name
  artist(mbid)      a bio line (type, area, life-span, tags) and the
                    discography: Albums, EPs, Singles, Live, Compilations,
                    each by date, with a Cover Art Archive picture link
  album(mbid)       a release group's tracklist, from its most canonical
                    release (official, earliest; else the first)
  artist_by_name(n) the artist a name surely is (a top match whose name is
                    the same, folded), for the song page's link

MusicBrainz asks for a User-Agent naming the app with a contact, and at
most one call a second (spaced here, `_get`). Lookups are kept a week on
disk (`<state_dir>/music-brainz.json`), searches a day. A track is played
by name through the host's YouTube search (music_discover.resolve), as
Discover's rows are.

`_get` is the one place that talks HTTP.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
import unicodedata
import urllib.parse
from pathlib import Path
from typing import Optional

MB = "https://musicbrainz.org/ws/2"
CAA_GROUP = "https://coverartarchive.org/release-group"
CAA_RELEASE = "https://coverartarchive.org/release"
USER_AGENT = "agent-media/1 ( https://github.com/southpenlabs/agent-media )"
GAP_S = 1.05
LOOKUP_TTL_S = 7 * 86400.0
SEARCH_TTL_S = 86400.0
#: Discography sections, in order.
SECTIONS = ("album", "ep", "single", "live", "compilation")

_lock = threading.Lock()
_last = [0.0]


def _get(url: str, timeout: float = 20.0) -> tuple[int, object]:
    """`(status, parsed JSON or None)`, a call at most every GAP_S."""
    import urllib.error
    import urllib.request
    with _lock:
        wait = _last[0] + GAP_S - time.monotonic()
        _last[0] = time.monotonic() + max(0.0, wait)
    if wait > 0:
        time.sleep(wait)
    req = urllib.request.Request(url, headers={"User-Agent": os.environ.get("MEDIA_MUSICBRAINZ_UA") or USER_AGENT,
                                               "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode() or "null")
    except urllib.error.HTTPError as e:
        return e.code, None
    except ValueError:
        return 200, None


def _path() -> Path:
    from ._paths import state_dir
    return state_dir() / "music-brainz.json"


_mem: dict = {}


def _kept(key: str, ttl: float, fn):
    if not _mem:
        try:
            d = json.loads(_path().read_text())
            _mem.update(d if isinstance(d, dict) else {})
        except (OSError, ValueError):
            pass
    hit = _mem.get(key)
    if isinstance(hit, dict) and time.time() - float(hit.get("at") or 0) < ttl:
        return hit["v"]
    v = fn()
    _mem[key] = {"at": time.time(), "v": v}
    if len(_mem) > 3000:
        for k in sorted(_mem, key=lambda k: _mem[k].get("at", 0))[:500]:
            _mem.pop(k, None)
    p = _path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(f".{p.name}.{os.getpid()}")
    tmp.write_text(json.dumps(_mem))
    os.replace(tmp, p)
    return v


def clear() -> None:
    _mem.clear()


def _ask(path: str, params: dict) -> dict:
    code, got = _get(f"{MB}/{path}?{urllib.parse.urlencode({**params, 'fmt': 'json'})}")
    if code == 404:
        raise LookupError("MusicBrainz has no such entry")
    if code != 200 or not isinstance(got, dict):
        raise OSError(f"MusicBrainz answered HTTP {code}")
    return got


def _fold(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def _credit(o: dict) -> str:
    return "".join(f"{c.get('name') or (c.get('artist') or {}).get('name') or ''}{c.get('joinphrase') or ''}"
                   for c in o.get("artist-credit") or [] if isinstance(c, dict)).strip()


def _year(d) -> Optional[int]:
    try:
        return int(str(d)[:4])
    except (TypeError, ValueError):
        return None


def _section(rg: dict) -> Optional[str]:
    second = [str(x).lower() for x in rg.get("secondary-types") or []]
    if "live" in second:
        return "live"
    if "compilation" in second:
        return "compilation"
    if second:
        return None  # soundtracks, remixes, interviews…: not shown
    return {"Album": "album", "EP": "ep", "Single": "single"}.get(str(rg.get("primary-type") or ""))


def _group(rg: dict) -> dict:
    return {"id": rg["id"], "title": str(rg.get("title") or ""), "artist": _credit(rg) or None,
            "type": _section(rg) or (str(rg.get("primary-type") or "").lower() or None),
            "date": rg.get("first-release-date") or None, "year": _year(rg.get("first-release-date")),
            "art": f"{CAA_GROUP}/{rg['id']}/front-250"}


def _artist_row(a: dict) -> dict:
    span = a.get("life-span") or {}
    return {"id": a["id"], "name": str(a.get("name") or ""), "type": a.get("type") or None,
            "disambiguation": a.get("disambiguation") or None,
            "area": (a.get("area") or {}).get("name") or a.get("country") or None,
            "begin": span.get("begin") or None, "end": span.get("end") or None,
            "ended": bool(span.get("ended")), "score": a.get("score")}


def search(q: str) -> dict:
    """`{artists: [...], albums: [...]}` for a name."""
    q = " ".join(str(q or "").split())[:200]
    if len(q) < 2:
        raise ValueError("search needs two letters or more")

    def ask():
        arts = _ask("artist", {"query": q, "limit": 6}).get("artists") or []
        rgs = _ask("release-group", {"query": q, "limit": 10}).get("release-groups") or []
        return {"artists": [_artist_row(a) for a in arts if isinstance(a, dict) and a.get("id")],
                "albums": [_group(r) for r in rgs if isinstance(r, dict) and r.get("id")]}
    return _kept(f"s:{q.lower()}", SEARCH_TTL_S, ask)


def _mbid(x: str) -> str:
    x = str(x or "").strip().lower()
    if not re.fullmatch(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", x):
        raise ValueError("not a MusicBrainz id")
    return x


def artist(mbid: str) -> dict:
    """An artist's page: `{artist: {..., tags}, sections: [{type, albums}]}`."""
    mbid = _mbid(mbid)

    def ask():
        a = _ask(f"artist/{mbid}", {"inc": "tags genres"})
        groups, offset = [], 0
        while offset < 300:
            got = _ask("release-group", {"artist": mbid, "limit": 100, "offset": offset})
            part = got.get("release-groups") or []
            groups += part
            offset += 100
            if len(part) < 100 or offset >= int(got.get("release-group-count") or 0):
                break
        row = _artist_row(a)
        tags = sorted((t for t in (a.get("genres") or []) + (a.get("tags") or []) if isinstance(t, dict) and t.get("name")),
                      key=lambda t: -int(t.get("count") or 0))
        seen: list[str] = []
        for t in tags:
            if t["name"] not in seen:
                seen.append(t["name"])
        row["tags"] = seen[:6]
        by: dict[str, list] = {s: [] for s in SECTIONS}
        for rg in groups:
            sec = _section(rg) if isinstance(rg, dict) and rg.get("id") else None
            if sec:
                by[sec].append(_group(rg))
        for s in by.values():
            s.sort(key=lambda g: (g["date"] or "9999"))
        return {"artist": row, "sections": [{"type": s, "albums": by[s]} for s in SECTIONS if by[s]]}
    return _kept(f"a:{mbid}", LOOKUP_TTL_S, ask)


def _canonical(releases: list[dict]) -> Optional[dict]:
    """Official first, then the earliest date; else the first listed."""
    rows = [r for r in releases if isinstance(r, dict) and r.get("id")]
    if not rows:
        return None
    official = [r for r in rows if str(r.get("status") or "").lower() == "official"] or rows
    dated = sorted(official, key=lambda r: (str(r.get("date") or "9999"), 0 if r.get("country") in ("XW", None) else 1))
    return dated[0]


def album(mbid: str) -> dict:
    """An album's page: `{album: {id, title, artist, year, type, art,
    release}, tracks: [{n, disc, title, artist, length_s}]}`."""
    mbid = _mbid(mbid)

    def ask():
        rg = _ask(f"release-group/{mbid}", {"inc": "artist-credits"})
        rels = _ask("release", {"release-group": mbid, "limit": 50}).get("releases") or []
        pick = _canonical(rels)
        tracks, rel_art = [], None
        if pick:
            rel = _ask(f"release/{pick['id']}", {"inc": "recordings artist-credits"})
            if (rel.get("cover-art-archive") or {}).get("front"):
                rel_art = f"{CAA_RELEASE}/{pick['id']}/front-250"
            for disc, m in enumerate(rel.get("media") or [], start=1):
                for t in m.get("tracks") or []:
                    rec = t.get("recording") or {}
                    ms = t.get("length") or rec.get("length")
                    tracks.append({"n": len(tracks) + 1, "disc": disc, "title": str(t.get("title") or rec.get("title") or ""),
                                   "artist": _credit(t) or _credit(rec) or _credit(rg) or None,
                                   "length_s": round(ms / 1000) if isinstance(ms, (int, float)) else None})
        g = _group(rg)
        g["art"] = rel_art or g["art"]
        g["release"] = pick["id"] if pick else None
        return {"album": g, "tracks": tracks}
    return _kept(f"r:{mbid}", LOOKUP_TTL_S, ask)


def artist_by_name(name: str) -> Optional[dict]:
    """The artist `name` surely is: MusicBrainz's top match, scored 95 or
    more, whose name is the same folded. None when unsure."""
    n = _fold(name)
    if len(n) < 2:
        return None

    def ask():
        arts = _ask("artist", {"query": f'artist:"{name}"', "limit": 3}).get("artists") or []
        top = next((a for a in arts if isinstance(a, dict) and a.get("id")), None)
        if top and int(top.get("score") or 0) >= 95 and _fold(top.get("name") or "") == n:
            return _artist_row(top)
        return None
    return _kept(f"n:{n}", LOOKUP_TTL_S, ask)
