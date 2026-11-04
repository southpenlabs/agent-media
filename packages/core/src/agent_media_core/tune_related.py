"""Songs of the listener's own that sit near some words: a reply's, a thread's.

docs/proposals/2026-11-03-reply-tune.md, "Related songs" (David, 4 Nov 2026:
"We could even maybe use the agent-sessions links to find other relations",
"maybe we can use music brains or something like that"). The reply's tune
(reply_tune.py) is the agent's own pick from what it knows of music. This
is the second way: the songs the listener has played, liked and asked for
(music_recent: `most`, `liked`), each written up as a short text — artist,
song, the catalogue's genre and MusicBrainz's community tags — and embedded
the way agent-sessions' zettel layer embeds its notes (zk_related.py:
nomic-embed-text through Ollama, the first 256 of its 768 dimensions, unit
length). `related(words)` embeds the words and returns the nearest songs;
`media tune related <words>` prints them, and `--session <id>` takes the
words from that thread's last replies, so a whole thread can keep a theme.

**Why these songs.** They are familiar: a song the listener already plays
does not pull them away from their thoughts on the reply the way a stranger
can (David, 4 Nov 2026). And the corpus is their own history, not a music
service's search: nothing from Spotify goes near a model (Developer Terms
v10 IV.2.1), and `[tune]` in agent_notes.md still bars a song picked from a
service's results.

**MusicBrainz.** Its data is CC0; the web service asks for a User-Agent
naming the app (MEDIA_MUSICBRAINZ_UA) and at most one request a second,
which `_mb_get` keeps to. A recording search (`artist:"…" AND
recording:"…"`) gives the recording's tags and genres and its artist's id;
the artist's own tags come from a second call (one per distinct artist,
cached). A song MusicBrainz does not know keeps the catalogue's genre.
Lookups are capped a run (`--lookups`, 20) so the index fills in over a few
runs rather than holding one up; a miss is not retried for a week.

**The index** is `<state_dir>/tune-related.json`: one entry a song (keyed
as music_recent does, by video id), with its text, its MusicBrainz tags and
its vector (base64 float32). A song whose text changed is re-embedded; the
rest never touch Ollama again. Without Ollama (MEDIA_TUNE_EMBED=off, or it
is not answering) `related` falls back to word overlap between the query
and each song's text, so the command still answers, less well.
"""

from __future__ import annotations

import base64
import json
import logging
import math
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from array import array
from pathlib import Path
from typing import Optional

from ._paths import state_dir

log = logging.getLogger(__name__)

INDEX = "tune-related.json"
DIM = 256
MB_BASE = "https://musicbrainz.org/ws/2"
MB_UA = "agent-media/0.1 (https://github.com/southpenlabs/agent-media)"
MB_MISS_RETRY_S = 7 * 86400.0
#: Longer than this and the "song" is a mix, a stream or a meditation, not a song.
MAX_SONG_CHARS = 60
#: A title that says it is one of those, whatever its length.
_NOT_SONG = re.compile(r"\b(mix|mixtape|playlist|meditation|hours?|hrs?|radio|live stream|"
                       r"compilation|full album|asmr)\b", re.I)
DEFAULT_LOOKUPS = 20
_last_mb_call = [0.0]


def index_path() -> Path:
    return state_dir() / INDEX


# ── the corpus: the listener's own plays ──────────────────────────────────────

def corpus(store=None, limit: int = 500) -> dict:
    """{key: {artist, song, genre, uri, title, plays, liked}} from the songs
    played most and the songs liked. A row with no artist or song, or a
    title too long to be one song or saying it is a mix, a meditation, a
    stream (`_NOT_SONG`), is left out."""
    from . import music_recent

    out: dict = {}
    for which in ("most", "liked"):
        try:
            rows = music_recent.listed("music", which, store=store, limit=limit)
        except Exception as e:  # noqa: BLE001
            log.debug("tune_related: %s list: %s", which, e)
            continue
        for r in rows:
            artist = (r.get("artist") or "").strip()
            song = (r.get("song") or "").strip()
            uri = r.get("uri") or ""
            if (not artist or not song or not uri or len(song) > MAX_SONG_CHARS
                    or _NOT_SONG.search(song)):
                continue
            key = music_recent._key(uri)
            ent = out.setdefault(key, {"artist": artist, "song": song, "genre": r.get("genre") or "",
                                       "uri": uri, "title": r.get("title") or "", "plays": 0,
                                       "liked": False})
            if which == "liked":
                ent["liked"] = True
            else:
                ent["plays"] = int(r.get("plays") or 1)
    return out


# ── MusicBrainz ───────────────────────────────────────────────────────────────

def _http_json(url: str, data: Optional[bytes] = None, headers: Optional[dict] = None,
               timeout: float = 20.0):
    req = urllib.request.Request(url, data=data, headers=headers or {})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.load(resp)


def _mb_get(path: str, params: dict) -> Optional[dict]:
    """One MusicBrainz call, no more than one a second, as its service asks."""
    wait = 1.05 - (time.monotonic() - _last_mb_call[0])
    if wait > 0:
        time.sleep(wait)
    _last_mb_call[0] = time.monotonic()
    url = f"{MB_BASE}/{path}?{urllib.parse.urlencode({**params, 'fmt': 'json'})}"
    ua = os.environ.get("MEDIA_MUSICBRAINZ_UA") or MB_UA
    try:
        return _http_json(url, headers={"User-Agent": ua, "Accept": "application/json"})
    except (urllib.error.URLError, OSError, ValueError) as e:
        log.debug("tune_related: musicbrainz %s: %s", path, e)
        return None


def _names(items) -> list[str]:
    out = []
    for it in items or []:
        name = (it.get("name") or "").strip().lower()
        if name and name not in out:
            out.append(name)
    return out


def _mb_quote(s: str) -> str:
    return '"' + s.replace('"', " ") + '"'


def musicbrainz_tags(artist: str, song: str, artist_cache: dict) -> dict:
    """{found, recording, artist_id, tags, genres, artist_tags} for a song.
    `artist_cache` ({artist_id: [tags]}) saves the artist call across songs."""
    q = f"artist:{_mb_quote(artist)} AND recording:{_mb_quote(song)}"
    got = _mb_get("recording", {"query": q, "limit": 3})
    if got is None:
        return {"found": False, "error": True}
    recs = [r for r in got.get("recordings") or [] if int(r.get("score") or 0) >= 80]
    if not recs:
        return {"found": False}
    rec = recs[0]
    credit = rec.get("artist-credit") or []
    artist_id = ((credit[0].get("artist") or {}).get("id") or "") if credit else ""
    out = {"found": True, "recording": rec.get("id") or "", "artist_id": artist_id,
           "tags": _names(rec.get("tags")), "genres": _names(rec.get("genres")),
           "artist_tags": []}
    if artist_id:
        if artist_id not in artist_cache:
            a = _mb_get(f"artist/{artist_id}", {"inc": "tags+genres"})
            artist_cache[artist_id] = (_names(a.get("genres")) + _names(a.get("tags"))) if a else []
        out["artist_tags"] = [t for t in artist_cache[artist_id] if t not in out["tags"]][:12]
    return out


# ── the text and the vectors ─────────────────────────────────────────────────

def song_text(ent: dict) -> str:
    """What a song is, in the words an embedding can place: artist, song,
    genre, then MusicBrainz's tags, the recording's before the artist's."""
    parts = [f"{ent['artist']} - {ent['song']}"]
    mb = ent.get("mb") or {}
    words = []
    for w in [ent.get("genre") or ""] + (mb.get("genres") or []) + (mb.get("tags") or []) \
            + (mb.get("artist_tags") or []):
        w = w.strip().lower()
        if w and w not in words:
            words.append(w)
    if words:
        parts.append(", ".join(words[:16]))
    return ". ".join(parts) + "."


def _unit(v: list[float]) -> list[float]:
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]


def embed_url() -> str:
    return (os.environ.get("MEDIA_TUNE_EMBED_URL") or os.environ.get("ZK_OLLAMA")
            or "http://127.0.0.1:11434")


def embed_model() -> str:
    return os.environ.get("MEDIA_TUNE_EMBED_MODEL") or "nomic-embed-text"


def embed_on() -> bool:
    return (os.environ.get("MEDIA_TUNE_EMBED") or "on").strip().lower() not in ("off", "0", "no")


def embed(texts: list[str], prefix: str = "search_document: ",
          timeout: float = 120.0) -> Optional[list[list[float]]]:
    """Unit vectors (DIM wide) for `texts`, through Ollama; None when it is
    off or not answering. nomic wants its task prefix on each text."""
    if not texts or not embed_on():
        return None
    out: list[list[float]] = []
    try:
        for i in range(0, len(texts), 8):
            body = json.dumps({"model": embed_model(),
                               "input": [prefix + t for t in texts[i:i + 8]]}).encode()
            got = _http_json(embed_url().rstrip("/") + "/api/embed", data=body,
                             headers={"Content-Type": "application/json"}, timeout=timeout)
            out += [_unit(v[:DIM]) for v in got["embeddings"]]
    except (urllib.error.URLError, OSError, ValueError, KeyError, TypeError) as e:
        log.debug("tune_related: embed: %s", e)
        return None
    return out


def _pack(v: list[float]) -> str:
    return base64.b64encode(array("f", v).tobytes()).decode()


def _unpack(s: str) -> list[float]:
    a = array("f")
    a.frombytes(base64.b64decode(s))
    return list(a)


def _words(text: str) -> set[str]:
    return set(re.findall(r"[a-z][a-z0-9]{2,}", text.lower()))


# ── the index ────────────────────────────────────────────────────────────────

def load() -> dict:
    p = index_path()
    try:
        d = json.loads(p.read_text())
        if isinstance(d, dict) and isinstance(d.get("songs"), dict):
            return d
    except (OSError, ValueError):
        pass
    return {"version": 1, "songs": {}, "artists": {}}


def save(d: dict) -> None:
    p = index_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(d, ensure_ascii=False))
    os.replace(tmp, p)


def index(store=None, lookups: int = DEFAULT_LOOKUPS, refresh: bool = False) -> dict:
    """Bring the index up to the listener's plays: new songs added, up to
    `lookups` of them asked of MusicBrainz (misses retried after a week;
    `refresh` asks again for every song), every changed text re-embedded.
    Returns counts: songs, looked_up, found, embedded, pending (songs
    still without a MusicBrainz answer), embed (whether Ollama answered)."""
    d = load()
    songs: dict = d["songs"]
    artists: dict = d.setdefault("artists", {})
    now = time.time()
    seen = corpus(store=store)
    for key in [k for k in songs if k not in seen]:
        del songs[key]
    looked = found = 0
    for key, fresh in seen.items():
        ent = songs.setdefault(key, {})
        for k, v in fresh.items():
            ent[k] = v
        mb = ent.get("mb")
        stale = (mb is None or refresh
                 or (not mb.get("found") and now - float(mb.get("at") or 0) > MB_MISS_RETRY_S))
        if stale and looked < lookups:
            got = musicbrainz_tags(ent["artist"], ent["song"], artists)
            looked += 1
            if got.get("error"):
                # Nothing to record: the service was not reached, so ask again next run.
                if mb is None:
                    ent["mb"] = {"found": False, "at": 0}
            else:
                got["at"] = now
                ent["mb"] = got
                found += int(bool(got.get("found")))
    todo = [k for k, e in songs.items() if e.get("text") != song_text(e) or not e.get("vec")]
    texts = [song_text(songs[k]) for k in todo]
    vecs = embed(texts) if todo else []
    embedded = 0
    if vecs is not None:
        for k, t, v in zip(todo, texts, vecs):
            songs[k]["text"] = t
            songs[k]["vec"] = _pack(v)
            embedded += 1
    else:
        for k, t in zip(todo, texts):
            songs[k]["text"] = t
    pending = sum(1 for e in songs.values()
                  if not (e.get("mb") or {}).get("found") and not float((e.get("mb") or {}).get("at") or 0))
    d["at"] = now
    save(d)
    return {"songs": len(songs), "looked_up": looked, "found": found, "embedded": embedded,
            "pending": pending, "embed": vecs is not None or not todo}


# ── the question ─────────────────────────────────────────────────────────────

def related(text: str, limit: int = 5, d: Optional[dict] = None) -> list[dict]:
    """The songs nearest `text`, best first: each the index's entry with
    `score` (cosine, or word overlap when there is no embedding) and `how`
    ("embed" or "words")."""
    text = (text or "").strip()
    d = d or load()
    songs = d.get("songs") or {}
    if not text or not songs:
        return []
    q = embed([text[:1500]], prefix="search_query: ")
    scored = []
    if q and any(e.get("vec") for e in songs.values()):
        qv = q[0]
        for key, e in songs.items():
            if not e.get("vec"):
                continue
            v = _unpack(e["vec"])
            scored.append((sum(a * b for a, b in zip(qv, v)), key, "embed"))
    else:
        qw = _words(text)
        for key, e in songs.items():
            sw = _words(e.get("text") or song_text(e))
            if not qw or not sw:
                continue
            scored.append((len(qw & sw) / math.sqrt(len(qw) * len(sw)), key, "words"))
    scored.sort(key=lambda t: (-round(t[0], 6), t[1]))
    out = []
    for score, key, how in scored[:limit]:
        e = dict(songs[key])
        e.pop("vec", None)
        e.update({"key": key, "score": round(score, 4), "how": how})
        out.append(e)
    return out


def session_text(session_id: str, store=None, replies: int = 6) -> str:
    """The last `replies` things said in a thread, newest last, as one text:
    what the thread is about, for `related`."""
    from .state import StateStore

    store = store or StateStore()
    sid = (session_id or "").strip()
    out: list[str] = []
    for row in store.recent_history(sink="speech", limit=400):
        extras = row.get("extras")
        if isinstance(extras, str):
            try:
                extras = json.loads(extras)
            except ValueError:
                extras = {}
        extras = extras if isinstance(extras, dict) else {}
        if extras.get("listener"):
            continue
        rs = str(extras.get("source_session") or extras.get("session") or "")
        if not rs or not (rs == sid or rs.startswith(sid)):
            continue
        t = (row.get("text") or "").strip()
        if t:
            out.append(t[:800])
        if len(out) >= replies:
            break
    out.reverse()
    return "\n\n".join(out)


def line(e: dict) -> str:
    """One song on a line: `Artist - Song  (0.61; rock, grunge)`."""
    mb = e.get("mb") or {}
    tags = [e.get("genre") or ""] + (mb.get("genres") or []) + (mb.get("tags") or [])
    tags = [t for t in dict.fromkeys(t.lower() for t in tags if t)][:4]
    why = f"; {', '.join(tags)}" if tags else ""
    return f"{e['artist']} - {e['song']}  ({e.get('score', 0):.2f}{why})"
