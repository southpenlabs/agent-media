"""A YouTube mix's chapters, as songs: each with its artist, album and genre.

David, 8 Nov 2026: "YouTube uploads are often mixes/playlists of various
artists, segmented into chapters." The chapters were already played (play-
local embeds them in the .mka; next and previous move by them, likes are per
chapter) but the Media tab's grouping looked only at the whole clip, so a
thirty-artist mix sat under its uploader. Here each chapter becomes a song:
its title parsed the way a clip's is (media_meta.parse_chapter: "Artist -
Song", "Song – Artist", "Song by Artist", numbers and timestamps dropped),
then looked up in the same catalogue, in the background, with the same
cache (media_meta). Recently played rows carry them as `chapters`, and the
app shows each under its artist, album or genre; a tap plays the clip from
that chapter's start.

Where the chapters come from: the file. The server cannot ask YouTube for
them (it is refused from a datacenter), so they are read from the copies
the players already have, with ffprobe: the rooms cache (on this host, or
the rooms host), then the phone's (one ssh for a batch, only where a phone
player is set up). Reads run in a background thread, a few videos a pass,
and what they find is kept in `music-chapters.json`; Sasonica's player
reading a file's chapters on a play (sinks/music_sasonica.chapters) keeps
them too. A video on neither is asked about again a day later; a file
without chapters never.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shlex
import subprocess
import threading
import time
from typing import Optional

from . import media_meta
from ._paths import state_dir

log = logging.getLogger(__name__)

_FILE = "music-chapters.json"
#: Videos read per background pass.
_PER_PASS = 12
#: A video neither cache had is looked for again after this long.
_MISSING_RETRY_S = 86400.0
#: A mix: at least this many chapters, naming at least two artists.
MIX_MIN_CHAPTERS = 3
MIX_MIN_ARTISTS = 2

_LOCK = threading.Lock()
_BUSY = threading.Event()
_VID = re.compile(r"^[A-Za-z0-9_-]{11}$")


def _load() -> dict:
    try:
        d = json.loads((state_dir() / _FILE).read_text())
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(d: dict) -> None:
    try:
        path = state_dir() / _FILE
        tmp = path.with_suffix(f".{os.getpid()}.{threading.get_ident()}.tmp")
        tmp.write_text(json.dumps(d))
        tmp.replace(path)
    except OSError:
        pass


def known(vid: str) -> Optional[list]:
    """A video's chapters, `[{"title", "start", "end"}]`, if they were read
    ([] for a file without any); None when not known yet."""
    with _LOCK:
        e = _load().get(vid)
    if isinstance(e, dict) and isinstance(e.get("chapters"), list):
        return e["chapters"]
    return None


def _clean_list(chs: list) -> list:
    out = []
    for c in chs or []:
        try:
            out.append({"title": str(c.get("title") or "").strip(),
                        "start": round(float(c["start"]), 3),
                        "end": round(float(c["end"]), 3) if c.get("end") is not None else None})
        except (KeyError, TypeError, ValueError, AttributeError):
            continue
    return out


def remember(vid: str, chapters: list) -> None:
    """Keep a video's chapters (`[{"title", "start", "end"}]`, [] for none)."""
    if not vid or not _VID.match(vid):
        return
    with _LOCK:
        d = _load()
        d[vid] = {"chapters": _clean_list(chapters), "at": time.time()}
        _save(d)


def remember_file(path_or_url: str, chapters: list) -> None:
    """Keep the chapters read from a cache file named `<video id>.<ext>`."""
    name = (path_or_url or "").rsplit("/", 1)[-1]
    vid = name.split(".", 1)[0]
    if _VID.match(vid):
        remember(vid, chapters)


# --- reading them from the caches -------------------------------------------------

def _probe_script(cache: str, vids: list[str]) -> str:
    """A shell snippet printing `@@<vid> <ffprobe json>` per video, the json
    empty when the cache has no file for it. Ids can start with `-`, hence
    `--` and `./`."""
    vs = " ".join(shlex.quote(v) for v in vids)
    return (f"cd \"$HOME\"/{shlex.quote(cache)} 2>/dev/null || exit 0; "
            f"for v in {vs}; do "
            "f=$(ls -1 -- \"$v\".* 2>/dev/null | grep -v -e '\\.title$' -e '\\.part$' -e '\\.json$' | head -1); "
            "printf '@@%s ' \"$v\"; "
            "if [ -n \"$f\" ]; then ffprobe -v quiet -print_format json -show_chapters \"./$f\" | tr -d '\\n'; fi; "
            "printf '\\n'; done")


def _parse_probe(out: str) -> dict:
    """`{vid: [chapters] | None}` from the snippet's output (None: no file)."""
    got: dict = {}
    for line in (out or "").splitlines():
        if not line.startswith("@@"):
            continue
        vid, _, raw = line[2:].partition(" ")
        if not _VID.match(vid):
            continue
        raw = raw.strip()
        if not raw:
            got[vid] = None
            continue
        try:
            chs = json.loads(raw).get("chapters") or []
        except ValueError:
            continue
        got[vid] = _clean_list([{"title": (c.get("tags") or {}).get("title"),
                                 "start": c.get("start_time"), "end": c.get("end_time")}
                                for c in chs if isinstance(c, dict)])
    return got


def _rooms_probe(vids: list[str]) -> Optional[dict]:
    from .sinks import music_fetch

    try:
        r = music_fetch._rooms_run(_probe_script(music_fetch.cache_dir(), vids), timeout=60)
    except (subprocess.TimeoutExpired, OSError):
        return None
    return _parse_probe(r.stdout) if r.returncode == 0 else None


def _phone_probe(vids: list[str]) -> Optional[dict]:
    """The phone's cache, only where a phone player is set up ({} without
    one: nothing there); None when it could not be asked."""
    from .sinks import music_local, music_sasonica

    if not (music_local.configured() or music_sasonica.configured()):
        return {}
    try:
        r = subprocess.run(music_local.phone_argv(_probe_script(music_local.cache_dir(), vids)),
                           capture_output=True, text=True, timeout=90)
    except (subprocess.TimeoutExpired, OSError):
        return None
    return _parse_probe(r.stdout) if r.returncode == 0 else None


def fill(vids: list[str]) -> None:
    """Read, in the background, the chapters of the videos in `vids` not yet
    known (or not found a day ago)."""
    if _BUSY.is_set():
        return
    with _LOCK:
        d = _load()
    now = time.time()
    todo = []
    for v in dict.fromkeys(vids):
        if not v or not _VID.match(v):
            continue
        e = d.get(v)
        if isinstance(e, dict) and (isinstance(e.get("chapters"), list)
                                    or now - float(e.get("at") or 0) < _MISSING_RETRY_S):
            continue
        todo.append(v)
    if not todo:
        return
    _BUSY.set()
    threading.Thread(target=_fill, args=(todo[:_PER_PASS],), daemon=True).start()


def _fill(vids: list[str]) -> None:
    try:
        found: dict = {}
        # A cache that could not be asked (the phone away) leaves the ones
        # not found to be asked about next time, not a day later.
        all_asked = True
        for probe in (_rooms_probe, _phone_probe):
            left = [v for v in vids if found.get(v) is None]
            if not left:
                break
            got = probe(left)
            if got is None:
                all_asked = False
                continue
            for v, chs in got.items():
                if chs is not None:
                    found[v] = chs
        now = time.time()
        with _LOCK:
            d = _load()
            for v in vids:
                if found.get(v) is not None:
                    d[v] = {"chapters": found[v], "at": now}
                elif all_asked:
                    d[v] = {"at": now}
            _save(d)
    except Exception as e:  # noqa: BLE001 — chapters are a nicety
        log.info("music-chapters: read failed: %s", e)
    finally:
        _BUSY.clear()


# --- as songs ------------------------------------------------------------------------

def _artist_key(a: Optional[str]) -> str:
    return re.sub(r"[^\w]", "", (a or "").lower())


def songs(vid: str, clip_artist: Optional[str] = None) -> tuple[list[dict], list]:
    """`(chapters, wanted)` for a video: its chapters as songs, `[{"index",
    "title", "start", "end", "artist", "song", "album", "genre"}]` ([] when
    it has fewer than two or they are not known yet), and the lookups still
    to make, for media_meta.fill.

    A chapter naming two names takes the catalogue's word for which is the
    artist; until it has one, the way round the mix's other chapters were
    found, else the clues in the titles (media_meta.parse_chapter), else
    "Artist - Song". A video none of whose chapters names an artist (a full
    album) gives each the clip's artist."""
    chs = known(vid) or []
    if len(chs) < 2:
        return [], []
    parsed = [media_meta.parse_chapter(c.get("title") or "") for c in chs]
    album_like = not any("a" in p or "artist" in p for p in parsed)
    wanted: list = []
    rows: list[dict] = []
    # Which way round this mix writes them: the catalogue's answers first.
    found_b = found_a = 0
    lean = 0
    for p in parsed:
        if "a" not in p:
            continue
        lean += p["lean"]
        got = media_meta.known(media_meta.track_key(p["a"], p["b"]))
        if got and got.get("found"):
            if _artist_key(got.get("artist")) == _artist_key(p["b"]):
                found_b += 1
            else:
                found_a += 1
    b_is_artist = found_b > found_a if (found_a or found_b) else lean > 0
    for i, (c, p) in enumerate(zip(chs, parsed)):
        artist = song = None
        key = None
        args: dict = {}
        if "a" in p:
            key = media_meta.track_key(p["a"], p["b"])
            args = {"a": p["a"], "b": p["b"], "lean": p["lean"]}
            artist, song = (p["b"], p["a"]) if b_is_artist else (p["a"], p["b"])
        elif "artist" in p:
            artist, song = p["artist"], p["song"]
            key = media_meta.track_key(artist, song, fixed=True)
            args = {"a": artist, "b": song, "fixed": True}
        else:
            song = p["song"]
            if album_like and clip_artist:
                artist = clip_artist
                key = media_meta.track_key(artist, song, fixed=True)
                args = {"a": artist, "b": song, "fixed": True}
        album = genre = None
        if key:
            got = media_meta.known(key)
            if got is None:
                wanted.append((key, "track", args))
            elif got.get("found"):
                artist = got.get("artist") or artist
                song = got.get("song") or song
                album, genre = got.get("album"), got.get("genre")
        rows.append({"index": i, "title": c.get("title") or "", "start": c.get("start") or 0.0,
                     "end": c.get("end"), "artist": artist or None, "song": song or None,
                     "album": album, "genre": genre})
    return rows, wanted


def is_mix(chapters: list[dict]) -> bool:
    """A mix: enough chapters, naming more than one artist."""
    artists = {_artist_key(c.get("artist")) for c in chapters if c.get("artist")}
    artists.discard("")
    return len(chapters) >= MIX_MIN_CHAPTERS and len(artists) >= MIX_MIN_ARTISTS
