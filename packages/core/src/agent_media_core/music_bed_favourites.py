"""The music bed from his favourites: instrumental versions of what he loves.

Roadmap item 39 (David, 30 Oct 2026: "I wonder if we could get instrumental
versions of favourite tracks for playing as background music for replies";
he chose both of the ways below). The fourth source in Settings → Music
under long readings, "Your favourites, instrumental".

**His favourites** (`gather`), at most once a day, the best
MEDIA_MUSIC_BED_FAVOURITES_MAX (30) by a score:

- the tracks he liked (`media music like`: the state store's `likes`,
  channel music), five points each;
- the tracks he asked for most (the music `history` rows the Media tab
  reads, YouTube ones counted by video id), a point a play;
- ListenBrainz, where Mopidy scrobbles, when
  MEDIA_MUSIC_BED_FAVOURITES_LISTENBRAINZ_USER names him: his all-time top
  recordings (a point a listen) and the ones he loved (five points). Public
  endpoints, no token.

Each is named by Mopidy's YouTube Music backend: a video id is looked up
(`find file`), an artist and title from ListenBrainz searched for, and only
a result with the same title and the artist kept (`_same_song`).

**1. Official instrumentals first** (`official`). Mopidy's YouTube Music
search, the route the streaming source uses, for "<artist> <title>
instrumental", then "... karaoke". A result is taken only when it is
confident (`is_instrumental_of`): the same title once the brackets are taken
off, the artist in its artist or title, a duration within 15 %
(MEDIA_MUSIC_BED_FAVOURITES_DURATION_TOLERANCE) of the original's, and a
title that says Instrumental, Karaoke or Backing track; never a cover, a
remix, nightcore, slowed or sped up. A karaoke or backing-track version is
usually a re-recording "in the style of" the artist, so it is credited
"(karaoke)", not "(official)"; MEDIA_MUSIC_BED_FAVOURITES_KARAOKE=0 takes
only those marked Instrumental. red5's address is bot-walled by
YouTube, so the track is fetched as every house play is: on the phone's
address into the rooms cache (`music_fetch.ensure_local`). A track searched
for and not found is not searched again for 30 days.

**2. Vocals removed for the rest** (`separate`). Demucs (MIT, a separate
process in its own venv, never imported and never a dependency: found at
MEDIA_MUSIC_BED_FAVOURITES_DEMUCS, else `$XDG_DATA_HOME/agent-media/
demucs-venv/bin/demucs`, else `demucs` on PATH) takes the original (fetched
the same way, cut to its first MEDIA_MUSIC_BED_FAVOURITES_KEEP_S, 300 s) and
keeps the `no_vocals` stem of `--two-stems vocals`. It is heavy, so it is
guarded:

- only in the nightly window (MEDIA_MUSIC_BED_FAVOURITES_WINDOW,
  ``02:00-05:00`` local; a window may cross midnight);
- at most MEDIA_MUSIC_BED_FAVOURITES_PER_NIGHT (3) attempts a night, one at a
  time (a lock), whether they work or not;
- never while the host is strained — memory stall (PSI full avg60) at or
  above MEDIA_MEM_STRAIN_PCT (10), as memwatch.py — or has less available
  memory than MEDIA_MUSIC_BED_FAVOURITES_MIN_FREE_MB (2000); it is skipped
  and tried the next night (or later in the window), and the reason shown;
- niced (19) and ioniced (idle), with MEDIA_MUSIC_BED_FAVOURITES_THREADS (2)
  torch threads, under a memory ceiling: `systemd-run --user --scope -p
  MemoryMax=<MEDIA_MUSIC_BED_FAVOURITES_MEMORY_MAX_MB, 2500>M -p
  MemorySwapMax=0`, so it is killed rather than pushed into swap. Measured
  on red5 (30 Oct 2026, htdemucs, 4 cores, 2 threads): a 3 min track took
  2 min 41 s and peaked at 1.76 GB (the cgroup's memory.peak); the ceiling
  leaves room for the 5 min cut.
  MEDIA_MUSIC_BED_FAVOURITES_GUARD=none runs it without the ceiling (for a
  host without a systemd user session); without systemd-run it is not run;
- a track that failed waits for the next night, and after three failures is
  given up on: nothing retries in a loop.

The window's work needs something to wake it: `python -m
agent_media_core.music_bed_favourites night`, from a timer of the host's own
(not shipped here), several times in the window; the music bed's background
process (`warm`) also does it when a reading comes in the window.

**Both kinds** are trimmed of leading silence, loudnormed to -30 LUFS and
cut to ten minutes (music_bed_library.normalise), kept in the speech clip
dir as `music-bed-fav-<key>.mp3`, reachable only as every clip is: by path
here and by a signed clip URL on his phone. Never published or shared
anywhere else; they are for his own listening of his own music. The index
(`<state_dir>/music-bed-favourites.json`) keeps each one's title, artist,
method (official or separated) and date. The cache is capped
(MEDIA_MUSIC_BED_FAVOURITES_MAX_MB, 300): when full nothing new is made, and
the least recently played go first if it is over.

**Playing**: a random ready track (Auto, or only the official or only the
separated ones) — weighted, when astrotunes' reading is fresh, by how near
its tempo and energy (its BPM and loudness, measured once as it is made,
`_measure`; held low from 21 to 6) are to the reading's — credited "Instrumental of <title> — <artist> (separated)".
Nothing ready: the generated bed, as the other sources.

**Only when chosen.** Nothing is gathered, searched or separated unless the
music bed's choice is this source (MEDIA_MUSIC_BED_FAVOURITES_PREPARE=always
prepares them anyway).

The tests never reach the network, Mopidy, the phone or Demucs: `_http`,
`_mpd`, `_fetch_local` and `_run` are patched in conftest.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import random
import re
import shutil
import subprocess
import sys
import time
import unicodedata
import urllib.parse
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

STYLES = ("official", "separated")
LABELS = {"official": "Official instrumentals only",
          "separated": "Vocals removed only"}
#: The credit's word for each method, shown in brackets after it.
METHOD_WORDS = {"official": "official", "separated": "separated"}

DEFAULT_MAX = 30
DEFAULT_MAX_MB = 300.0
DEFAULT_PER_NIGHT = 3
DEFAULT_WINDOW = "02:00-05:00"
DEFAULT_MEMORY_MAX_MB = 2500
DEFAULT_MIN_FREE_MB = 2000
DEFAULT_THREADS = 2
DEFAULT_KEEP_S = 300
DEFAULT_TOLERANCE = 0.15
DEFAULT_MODEL = "htdemucs"
DEFAULT_STRAIN_PCT = 10.0
#: How long Demucs may take for one track before it is stopped.
SEPARATE_TIMEOUT_S = 3600
FAVOURITES_TTL_S = 24 * 3600.0
OFFICIAL_RETRY_S = 30 * 86400.0
#: The least time between two passes of the background process (searching
#: for official versions), so a busy afternoon of replies is one pass.
PASS_GAP_S = 6 * 3600.0
MAX_FAILURES = 3
#: Official versions fetched in one pass, and searches made.
OFFICIAL_FETCHES = 3
OFFICIAL_SEARCHES = 8
MIN_TRACK_S = 60
MAX_TRACK_S = 1200
LIKE_POINTS = 5

LISTENBRAINZ = "https://api.listenbrainz.org/1"
USER_AGENT = ("agent-media-music-bed/1.0 (+https://github.com/southpenlabs/agent-media; "
              "a listener's own favourites)")

_MARK = re.compile(r"\b(instrumentals?|karaoke|backing tracks?)\b", re.I)
_NEVER = re.compile(r"\b(covers?|covered|remix(es|ed)?|rmx|nightcore|slowed|sped[ -]up|"
                    r"8d|lullaby|8[ -]bit|tribute)\b", re.I)
#: A bracket on an original's title that is about the upload, not the song.
_BRACKETS = re.compile(r"\s*[\(\[\{][^\)\]\}]*[\)\]\}]")
#: A " - suffix" that is about the version, not the song.
_SUFFIX = re.compile(r"\s+[-–—]\s+[^-–—]*\b(instrumental|karaoke|backing|remaster(ed)?|"
                     r"official|version|edit|mix|audio|video|lyrics?|live|mono|stereo)\b"
                     r"[^-–—]*$", re.I)


# ---- settings ------------------------------------------------------------------------------

def _num_env(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, "") or default)
    except ValueError:
        return default


def _off(name: str) -> bool:
    return (os.environ.get(name) or "").strip().lower() in ("0", "off", "false", "no")


def enabled() -> bool:
    return not _off("MEDIA_MUSIC_BED_FAVOURITES")


def separate_on() -> bool:
    return not _off("MEDIA_MUSIC_BED_FAVOURITES_SEPARATE")


def prepare_always() -> bool:
    return (os.environ.get("MEDIA_MUSIC_BED_FAVOURITES_PREPARE") or "").strip().lower() == "always"


def max_favourites() -> int:
    return max(1, int(_num_env("MEDIA_MUSIC_BED_FAVOURITES_MAX", DEFAULT_MAX)))


def max_bytes() -> int:
    return int(_num_env("MEDIA_MUSIC_BED_FAVOURITES_MAX_MB", DEFAULT_MAX_MB) * 1024 * 1024)


def per_night() -> int:
    return max(0, int(_num_env("MEDIA_MUSIC_BED_FAVOURITES_PER_NIGHT", DEFAULT_PER_NIGHT)))


def memory_max_mb() -> int:
    return max(256, int(_num_env("MEDIA_MUSIC_BED_FAVOURITES_MEMORY_MAX_MB",
                                 DEFAULT_MEMORY_MAX_MB)))


def min_free_mb() -> int:
    return max(0, int(_num_env("MEDIA_MUSIC_BED_FAVOURITES_MIN_FREE_MB", DEFAULT_MIN_FREE_MB)))


def tolerance() -> float:
    return max(0.0, _num_env("MEDIA_MUSIC_BED_FAVOURITES_DURATION_TOLERANCE",
                             DEFAULT_TOLERANCE))


def karaoke_on() -> bool:
    """Whether a karaoke or backing-track version counts (they are usually
    re-recordings "in the style of" the artist, credited as karaoke);
    MEDIA_MUSIC_BED_FAVOURITES_KARAOKE=0 takes only those marked
    Instrumental."""
    return not _off("MEDIA_MUSIC_BED_FAVOURITES_KARAOKE")


def listenbrainz_user() -> str:
    """His ListenBrainz name: the personal settings' (personal_prefs, where
    there is one), else MEDIA_MUSIC_BED_FAVOURITES_LISTENBRAINZ_USER."""
    try:
        from . import personal_prefs
        got = personal_prefs.get("listenbrainz.user")
        if got:
            return str(got).strip()
    except Exception:  # noqa: BLE001 — no personal settings here
        pass
    return (os.environ.get("MEDIA_MUSIC_BED_FAVOURITES_LISTENBRAINZ_USER") or "").strip()


def model() -> str:
    return (os.environ.get("MEDIA_MUSIC_BED_FAVOURITES_MODEL") or DEFAULT_MODEL).strip()


def guard() -> str:
    g = (os.environ.get("MEDIA_MUSIC_BED_FAVOURITES_GUARD") or "systemd").strip().lower()
    return "none" if g in ("none", "off", "0") else "systemd"


def demucs_exe() -> Optional[str]:
    """Demucs's command: MEDIA_MUSIC_BED_FAVOURITES_DEMUCS, else the venv
    under XDG_DATA_HOME/agent-media, else `demucs` on PATH; None without."""
    named = (os.environ.get("MEDIA_MUSIC_BED_FAVOURITES_DEMUCS") or "").strip()
    if named:
        return named if (Path(named).is_file() or shutil.which(named)) else None
    base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    venv = Path(base) / "agent-media" / "demucs-venv" / "bin" / "demucs"
    if venv.is_file():
        return str(venv)
    return shutil.which("demucs")


def window() -> tuple[int, int]:
    """The nightly window as (start, end) minutes after midnight."""
    raw = (os.environ.get("MEDIA_MUSIC_BED_FAVOURITES_WINDOW") or DEFAULT_WINDOW).strip()
    try:
        a, b = raw.split("-")
        start = [int(x) for x in a.strip().split(":")]
        end = [int(x) for x in b.strip().split(":")]
        s = start[0] * 60 + (start[1] if len(start) > 1 else 0)
        e = end[0] * 60 + (end[1] if len(end) > 1 else 0)
        if 0 <= s < 1440 and 0 <= e <= 1440 and s != e:
            return s, e
    except (ValueError, IndexError):
        pass
    log.info("music-bed favourites: window %r is not HH:MM-HH:MM; %s", raw, DEFAULT_WINDOW)
    return 120, 300


def window_words() -> str:
    s, e = window()
    return f"{s // 60:02d}:{s % 60:02d}–{e // 60:02d}:{e % 60:02d}"


def in_window(when: Optional[datetime] = None) -> bool:
    when = when or datetime.now()
    s, e = window()
    m = when.hour * 60 + when.minute
    return s <= m < e if s < e else (m >= s or m < e)


def night_of(when: Optional[datetime] = None) -> str:
    """The night a moment belongs to, named by the date its window starts."""
    when = when or datetime.now()
    s, _ = window()
    return (when - timedelta(minutes=s)).strftime("%Y-%m-%d")


def next_window(when: Optional[datetime] = None) -> datetime:
    """When the window next opens (now, if it is open)."""
    when = when or datetime.now()
    if in_window(when):
        return when
    s, _ = window()
    start = when.replace(hour=s // 60, minute=s % 60, second=0, microsecond=0)
    return start if start > when else start + timedelta(days=1)


# ---- the index -------------------------------------------------------------------------------

def _index_path() -> Path:
    from ._paths import state_dir
    return state_dir() / "music-bed-favourites.json"


def _lock_path() -> Path:
    from ._paths import state_dir
    return state_dir() / "music-bed-favourites.lock"


def _dir() -> Path:
    from ._paths import speech_audio_dir
    return speech_audio_dir()


def _work_dir() -> Path:
    from ._paths import state_dir
    return state_dir() / "music-bed-favourites"


def load() -> dict:
    try:
        data = json.loads(_index_path().read_text())
        if isinstance(data, dict):
            for k, v in (("favourites", []), ("tracks", []), ("state", {}),
                         ("nights", {}), ("lookups", {})):
                data.setdefault(k, v)
            return data
    except (OSError, ValueError):
        pass
    return {"favourites": [], "tracks": [], "state": {}, "nights": {}, "lookups": {}}


def save(data: dict) -> None:
    p = _index_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(f".{p.name}.{os.getpid()}")
    tmp.write_text(json.dumps(data, indent=1))
    os.replace(tmp, p)


def _file(entry: dict) -> Path:
    return _dir() / entry["file"]


def tracks(style: Optional[str] = None, data: Optional[dict] = None) -> list[dict]:
    """The ready tracks (of `style`: official, separated; None or auto for
    both) whose files are there."""
    data = load() if data is None else data
    out = []
    for e in data["tracks"]:
        if style in STYLES and e.get("method") != style:
            continue
        try:
            if _file(e).is_file():
                out.append(e)
        except (OSError, KeyError):
            continue
    return out


def counts(data: Optional[dict] = None) -> dict:
    data = load() if data is None else data
    return {s: len(tracks(s, data)) for s in STYLES}


def _cache_bytes(data: dict) -> int:
    total = 0
    for e in tracks(None, data):
        try:
            total += _file(e).stat().st_size
        except OSError:
            pass
    return total


def cache_full(data: Optional[dict] = None) -> bool:
    return _cache_bytes(load() if data is None else data) >= max_bytes()


def credit(entry: dict) -> dict:
    """What the app shows: "Instrumental of <title>" by the artist, the
    method where a library track has its licence."""
    vid = entry.get("original_id") or ""
    method = entry.get("method") or ""
    word = "karaoke" if entry.get("karaoke") else METHOD_WORDS.get(method, method)
    return {"title": f"Instrumental of {entry.get('title') or '?'}",
            "creator": entry.get("artist") or "",
            "license": word, "license_url": "",
            "url": f"https://music.youtube.com/watch?v={vid}" if vid else "",
            "source": "Your favourites", "method": method,
            "original_title": entry.get("title") or "",
            "made": entry.get("made") or ""}


def features_fit(entry: dict, axes: Optional[dict], late: bool = False) -> float:
    """How well a track's tempo and energy suit astrotunes' reading: a
    weight, higher is nearer; 1 for a track never measured or no reading.
    Late at night the wanted tempo and energy are held at 0.4 or under."""
    if not axes:
        return 1.0
    f = entry.get("features") or {}
    if "tempo" not in f or "energy" not in f:
        return 1.0
    want_t = float(axes.get("tempo", 0.5))
    want_e = float(axes.get("energy", 0.5))
    if late:
        want_t, want_e = min(want_t, 0.4), min(want_e, 0.4)
    return 1.0 / (0.15 + abs(f["tempo"] - want_t) + abs(f["energy"] - want_e))


def track_for(style: str = "auto", axes: Optional[dict] = None,
              when: Optional[datetime] = None) -> Optional[tuple[Path, dict]]:
    """A ready track (of `style`) and its credit; None with none. At random,
    weighted by fit to astrotunes' tempo and energy when `axes` are given
    (features_fit), so the nearest come up most but not always. Marks it
    played. Never fetches."""
    try:
        have = tracks(style)
        if not have:
            return None
        hour = (when or datetime.now()).hour
        late = hour >= 21 or hour < 6
        weights = [features_fit(e, axes, late) for e in have]
        e = random.choices(have, weights=weights, k=1)[0]
        p = _file(e)
        try:
            os.utime(p)
        except OSError:
            pass
        return p, credit(e)
    except Exception as e:  # noqa: BLE001 — a bed must never cost a reply
        log.warning("music-bed favourites: %s", e)
        return None


# ---- names and matching ----------------------------------------------------------------------

def norm(s) -> str:
    """Lower case, accents off, `&` as "and", punctuation as spaces."""
    s = unicodedata.normalize("NFKD", str(s or ""))
    s = "".join(c for c in s if not unicodedata.combining(c)).lower()
    s = s.replace("&", " and ").replace("’", "'").replace("'", "")
    return " ".join(re.sub(r"[^0-9a-z]+", " ", s).split())


def base_title(title: str) -> str:
    """A title without its brackets and version suffix, normalised:
    "Angel (Remastered 2019)" and "Angel - Instrumental" are both "angel"."""
    t = _BRACKETS.sub("", str(title or ""))
    t = _SUFFIX.sub("", t)
    return norm(t)


def _titles(title: str) -> set[str]:
    """The base titles a result could mean: itself, and what follows an
    "Artist - " in it."""
    out = {base_title(title)}
    for sep in (" - ", " – ", " — "):
        if sep in title:
            out.add(base_title(title.split(sep, 1)[1]))
    out.discard("")
    return out


def _artist_in(artist: str, *where: str) -> bool:
    a = norm(artist)
    if not a:
        return False
    a = re.sub(r"^the ", "", a)
    return any(f" {a} " in f" {norm(w)} " for w in where)


def clean_original(artist: str, title: str) -> tuple[str, str]:
    """An original's artist and title from a YouTube upload's: "Wolfmother -
    Joker And The Thief (Audio)" by "WolfmotherVEVO" is Wolfmother's "Joker
    And The Thief"; `Phantogram "When I'm Small"` is Phantogram's "When I'm
    Small"."""
    artist = re.sub(r"\s*-\s*Topic$|VEVO$", "", str(artist or "")).strip()
    title = str(title or "").strip()
    q = re.match(r'^(.*?)\s*["“]([^"”]+)["”]', title)
    if q:
        lead = q.group(1).strip(" -–—")
        if lead and not (artist and _artist_in(artist, lead)):
            artist = lead
        title = q.group(2)
    for sep in (" - ", " – ", " — "):
        if sep in title:
            left, right = title.split(sep, 1)
            if not artist or _artist_in(artist, left) or _artist_in(left, artist):
                artist, title = left.strip(), right.strip()
            break
    title = _BRACKETS.sub("", title).strip() or title
    return artist, title


def is_instrumental_of(orig: dict, cand: dict, tol: Optional[float] = None) -> bool:
    """Whether search result `cand` ({title, artist, dur}) is confidently an
    official instrumental of `orig` ({title, artist, dur}): marked
    Instrumental, Karaoke or Backing track, never a cover or remix, the same
    title, the artist named, and within `tol` (15 %) of its length."""
    tol = tolerance() if tol is None else tol
    t, a = str(cand.get("title") or ""), str(cand.get("artist") or "")
    if not _MARK.search(t) or _NEVER.search(t) or _NEVER.search(a):
        return False
    if not karaoke_on() and not re.search(r"\binstrumental", t, re.I):
        return False
    want = base_title(orig.get("title") or "")
    if not want or want not in _titles(t):
        return False
    if not _artist_in(orig.get("artist") or "", a, t):
        return False
    od, cd = float(orig.get("dur") or 0), float(cand.get("dur") or 0)
    return od > 0 and cd > 0 and abs(cd - od) <= tol * od


def is_original(orig: dict, cand: dict) -> bool:
    """Whether `cand` is the song `orig` names (from ListenBrainz: an artist
    and title, no length): the same title, the artist, and not itself an
    instrumental, karaoke, cover, remix or live take."""
    t, a = str(cand.get("title") or ""), str(cand.get("artist") or "")
    if _MARK.search(t) or _NEVER.search(t) or re.search(r"\blive\b", t, re.I):
        return False
    want = base_title(orig.get("title") or "")
    return bool(want) and want in _titles(t) and _artist_in(orig.get("artist") or "", a, t)


def key_for(artist: str, title: str) -> str:
    return hashlib.sha1(f"{norm(artist)}|{base_title(title)}".encode()).hexdigest()[:12]


# ---- the outside world (patched in the tests) ----------------------------------------------

def _mpd(*lines: str) -> list[str]:
    from .sinks import music
    from .types import Target
    with music._connect(Target(name="local")) as s:
        return [music._cmd(s, line) for line in lines]


def _fetch_local(vid: str) -> Optional[str]:
    """The track's file in the rooms cache, fetched on the phone's address
    if need be (sinks/music_fetch.py). Slow."""
    from .sinks import music_fetch
    return music_fetch.ensure_local(f"https://www.youtube.com/watch?v={vid}")


def _http(url: str, timeout: float = 20.0) -> bytes:
    import urllib.request
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read(4 * 1024 * 1024)


def _run(cmd: list[str], timeout: float, env: Optional[dict] = None) -> int:
    """Run `cmd` and wait; its exit code (124 for a timeout)."""
    try:
        r = subprocess.run(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                           stderr=subprocess.PIPE, timeout=timeout, env=env)
    except subprocess.TimeoutExpired:
        return 124
    if r.returncode:
        log.info("music-bed favourites: %s exited %s: %s", cmd[0], r.returncode,
                 r.stderr.decode(errors="replace")[-400:])
    return r.returncode


def _host_mem() -> dict:
    """`{"mem_available_mb", "mem_stall_pct"}` from /proc (None when not
    readable), as procmem.host_mem in the server."""
    avail = None
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemAvailable:"):
                avail = round(int(line.split()[1]) / 1024)
                break
    except (OSError, ValueError, IndexError):
        pass
    stall = None
    try:
        for line in Path("/proc/pressure/memory").read_text().splitlines():
            if line.startswith("full"):
                for part in line.split():
                    if part.startswith("avg60="):
                        stall = float(part[6:])
    except (OSError, ValueError):
        pass
    return {"mem_available_mb": avail, "mem_stall_pct": stall}


# ---- Mopidy's YouTube Music ----------------------------------------------------------------

def _q(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"')


def _songs(raw: str) -> list[dict]:
    """Mopidy's listing as `{id, title, artist, dur}` for YouTube videos."""
    rows, cur = [], None
    for line in raw.splitlines():
        k, _, v = line.partition(": ")
        if k == "file":
            cur = {"file": v, "artists": []}
            rows.append(cur)
        elif cur is not None:
            if k == "Artist":
                cur["artists"].append(v)
            else:
                cur.setdefault(k, v)
    out = []
    for r in rows:
        f = r.get("file", "")
        if not f.startswith("youtube:video:"):
            continue
        try:
            dur = int(float(r.get("Time") or 0))
        except ValueError:
            dur = 0
        out.append({"id": f.rsplit(":", 1)[1], "title": r.get("Title") or "",
                    "artist": ", ".join(r["artists"]), "dur": dur})
    return out


def search(query: str) -> list[dict]:
    return _songs(_mpd(f'search any "{_q(query)}"')[0])


def lookup(vid: str, data: Optional[dict] = None) -> Optional[dict]:
    """A video's `{id, artist, title, dur}`, cleaned (clean_original), from
    `data`'s lookups or Mopidy."""
    if data is not None and vid in data["lookups"]:
        return data["lookups"][vid]
    try:
        got = _songs(_mpd(f'find file "youtube:video:{_q(vid)}"')[0])
    except Exception as e:  # noqa: BLE001
        log.info("music-bed favourites: lookup %s failed: %s", vid, e)
        return None
    if not got:
        return None
    s = got[0]
    artist, title = clean_original(s["artist"], s["title"])
    out = {"id": vid, "artist": artist, "title": title, "dur": s["dur"]}
    if data is not None:
        data["lookups"][vid] = out
    return out


# ---- his favourites ----------------------------------------------------------------------------

def _vid(uri: str) -> Optional[str]:
    try:
        from .sinks.music_fetch import watch_id
        return watch_id(uri.split("yt:", 1)[-1]) if uri else None
    except Exception:  # noqa: BLE001
        return None


def _from_store() -> dict[str, dict]:
    """{video id: {"likes", "plays"}} from the state store."""
    out: dict[str, dict] = {}
    try:
        from .state import StateStore
        st = StateStore()
        for r in st.list_likes(limit=500, channel="music"):
            v = _vid(r.get("media_id") or "") or _vid(r.get("uri") or "")
            if v:
                out.setdefault(v, {"likes": 0, "plays": 0})["likes"] += 1
        for r in st.recent_history(sink="music", limit=5000, requested=True):
            v = _vid(r.get("uri") or "")
            if v:
                out.setdefault(v, {"likes": 0, "plays": 0})["plays"] += 1
    except Exception as e:  # noqa: BLE001
        log.info("music-bed favourites: the state store: %s", e)
    return out


def _from_listenbrainz(user: str) -> list[dict]:
    """[{artist, title, plays, loved}] from his ListenBrainz stats and loves."""
    if not user:
        return []
    u = urllib.parse.quote(user)
    out = []
    try:
        d = json.loads(_http(f"{LISTENBRAINZ}/stats/user/{u}/recordings?range=all_time&count=100"))
        for r in (d.get("payload") or {}).get("recordings") or []:
            if r.get("artist_name") and r.get("track_name"):
                out.append({"artist": r["artist_name"], "title": r["track_name"],
                            "plays": int(r.get("listen_count") or 0), "loved": False})
    except Exception as e:  # noqa: BLE001 — 204 (no stats yet) lands here too
        log.info("music-bed favourites: ListenBrainz stats: %s", e)
    try:
        d = json.loads(_http(f"{LISTENBRAINZ}/feedback/user/{u}/get-feedback"
                             f"?score=1&count=100&metadata=true"))
        for r in d.get("feedback") or []:
            md = r.get("track_metadata") or {}
            if md.get("artist_name") and md.get("track_name"):
                out.append({"artist": md["artist_name"], "title": md["track_name"],
                            "plays": 0, "loved": True})
    except Exception as e:  # noqa: BLE001
        log.info("music-bed favourites: ListenBrainz loves: %s", e)
    return out


def gather(data: dict, now: Optional[float] = None) -> list[dict]:
    """His favourites, best first, named and with a video and its length:
    `[{key, artist, title, id, dur, score, from}]`. Kept in `data`."""
    now = time.time() if now is None else now
    merged: dict[str, dict] = {}

    def add(song: dict, score: float, where: str) -> None:
        if not song or not song.get("title") or not song.get("artist"):
            return
        if not (MIN_TRACK_S <= int(song.get("dur") or 0) <= MAX_TRACK_S):
            return
        k = key_for(song["artist"], song["title"])
        cur = merged.get(k)
        if cur is None:
            merged[k] = cur = {"key": k, "artist": song["artist"], "title": song["title"],
                               "id": song["id"], "dur": int(song["dur"]), "score": 0.0,
                               "from": []}
        cur["score"] += score
        if where not in cur["from"]:
            cur["from"].append(where)

    store = _from_store()
    ranked = sorted(store.items(), key=lambda kv: -(kv[1]["likes"] * LIKE_POINTS + kv[1]["plays"]))
    for vid, n in ranked[: max_favourites() * 3]:
        song = lookup(vid, data)
        if n["likes"]:
            add(song, n["likes"] * LIKE_POINTS, "liked")
        if n["plays"]:
            add(song, n["plays"], "played")
    lb = _from_listenbrainz(listenbrainz_user())
    lb.sort(key=lambda r: -(r["plays"] + (LIKE_POINTS if r["loved"] else 0)))
    for r in lb[: max_favourites() * 2]:
        k = key_for(r["artist"], r["title"])
        song = None
        if k in merged:
            song = merged[k]
        else:
            cached = data["lookups"].get(f"lb:{k}")
            if cached is not None:
                song = cached or None
            else:
                try:
                    song = next((s for s in search(f"{r['artist']} {r['title']}")
                                 if is_original(r, s)), None)
                except Exception as e:  # noqa: BLE001
                    log.info("music-bed favourites: search failed: %s", e)
                    continue
                if song is not None:
                    song = {"id": song["id"], "artist": r["artist"], "title": r["title"],
                            "dur": song["dur"]}
                data["lookups"][f"lb:{k}"] = song or {}
        if song:
            add(song, r["plays"] + (LIKE_POINTS if r["loved"] else 0),
                "listenbrainz-loved" if r["loved"] else "listenbrainz")
    favs = sorted(merged.values(), key=lambda f: -f["score"])[: max_favourites()]
    data["favourites"] = favs
    data["favourites_at"] = now
    # The lookups are a cache; keep it from growing without end.
    if len(data["lookups"]) > 2000:
        data["lookups"] = dict(list(data["lookups"].items())[-1000:])
    return favs


def _has_track(data: dict, key: str) -> bool:
    return any(e.get("key") == key for e in tracks(None, data))


def _state(data: dict, key: str) -> dict:
    return data["state"].setdefault(key, {})


# ---- making a bed from a track -------------------------------------------------------------

def _normalise(src: Path, dst: Path) -> bool:
    from .music_bed_library import normalise
    return normalise(src, dst)


def bpm_of(env: list[float], rate: float) -> Optional[float]:
    """The tempo of an onset envelope (`rate` frames a second): the lag of
    the strongest autocorrelation between 60 and 180 BPM."""
    n = len(env)
    if n < rate * 10:
        return None
    onset = [max(0.0, env[i] - env[i - 1]) for i in range(1, n)]
    mean = sum(onset) / len(onset)
    onset = [x - mean for x in onset]
    def corr(lag: int) -> float:
        return sum(onset[i] * onset[i - lag] for i in range(lag, len(onset)))
    best, best_lag = 0.0, None
    for lag in range(int(rate * 60 / 180), int(rate * 60 / 60) + 1):
        c = corr(lag)
        if c > best:
            best, best_lag = c, lag
    if not best_lag:
        return None
    # A beat found at half the tempo (the usual mistake): the double when
    # it is nearly as strong.
    half = round(best_lag / 2)
    if 60.0 * rate / half <= 180 and corr(half) >= 0.6 * best:
        best_lag = half
    return round(60.0 * rate / best_lag, 1)


def _measure(src: Path) -> dict:
    """A track's tempo and loudness, measured once as it is made: `{"bpm",
    "lufs", "tempo", "energy"}` (the last two 0..1 like astrotunes' axes:
    60 → 180 BPM, -20 → -8 LUFS), or {} when ffmpeg cannot say."""
    out: dict = {}
    try:
        r = subprocess.run(["ffmpeg", "-nostdin", "-hide_banner", "-nostats", "-t", "90",
                            "-i", str(src), "-af", "ebur128", "-f", "null", "-"],
                           stdin=subprocess.DEVNULL, capture_output=True, timeout=120)
        m = re.findall(r"I:\s+(-?[0-9.]+) LUFS", r.stderr.decode(errors="replace"))
        if m:
            out["lufs"] = float(m[-1])
        rate = 100
        r = subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-t", "90",
                            "-i", str(src), "-ac", "1", "-ar", "8000", "-f", "s16le", "-"],
                           stdin=subprocess.DEVNULL, capture_output=True, timeout=120)
        import array
        pcm = array.array("h")
        pcm.frombytes(r.stdout[: len(r.stdout) // 2 * 2])
        if sys.byteorder != "little":
            pcm.byteswap()
        hop = 8000 // rate
        env = [sum(abs(x) for x in pcm[i:i + hop]) / hop for i in range(0, len(pcm) - hop, hop)]
        bpm = bpm_of(env, rate)
        if bpm:
            out["bpm"] = bpm
    except (OSError, subprocess.SubprocessError, ValueError) as e:
        log.info("music-bed favourites: measuring %s: %s", src, e)
    if "bpm" in out:
        out["tempo"] = round(max(0.0, min(1.0, (out["bpm"] - 60) / 120)), 2)
    if "lufs" in out:
        out["energy"] = round(max(0.0, min(1.0, (out["lufs"] + 20) / 12)), 2)
    return out


def _add(data: dict, fav: dict, src: Path, method: str, inst_id: str = "",
         karaoke: bool = False) -> Optional[dict]:
    name = f"music-bed-fav-{fav['key']}.mp3"
    dst = _dir() / name
    dst.parent.mkdir(parents=True, exist_ok=True)
    # Measured before loudnorm, which would make every track as loud.
    features = _measure(src)
    if not _normalise(src, dst):
        return None
    entry = {"key": fav["key"], "file": name, "title": fav["title"], "artist": fav["artist"],
             "method": method, "original_id": fav.get("id") or "", "instrumental_id": inst_id,
             "made": datetime.now().strftime("%Y-%m-%d"), "added": time.time(),
             "bytes": dst.stat().st_size, "features": features}
    if karaoke:
        entry["karaoke"] = True
    data["tracks"] = [e for e in data["tracks"] if e.get("key") != fav["key"]] + [entry]
    prune(data)
    return entry


def prune(data: dict, cap: Optional[int] = None) -> dict:
    """Entries whose files are gone dropped, then the least recently played
    until the cache is under `cap` bytes."""
    cap = max_bytes() if cap is None else cap
    live = []
    for e in data["tracks"]:
        try:
            st = _file(e).stat()
            live.append((st.st_mtime, st.st_size, e))
        except (OSError, KeyError):
            continue
    live.sort(key=lambda x: x[0])
    total = sum(size for _, size, _ in live)
    while len(live) > 1 and total > cap:
        _, size, e = live.pop(0)
        try:
            _file(e).unlink()
        except OSError:
            pass
        total -= size
        log.info("music-bed favourites: pruned %s", e.get("file"))
    data["tracks"] = [e for _, _, e in live]
    return data


def official(data: dict, now: Optional[float] = None,
             fetches: int = OFFICIAL_FETCHES, searches: int = OFFICIAL_SEARCHES) -> int:
    """Look for official instrumentals of the favourites that have no bed
    yet; fetch and keep the confident ones. How many came."""
    now = time.time() if now is None else now
    got = 0
    for fav in data["favourites"]:
        if fetches <= 0 or searches <= 0 or cache_full(data):
            break
        if _has_track(data, fav["key"]):
            continue
        st = _state(data, fav["key"])
        if "official_at" in st and now - float(st["official_at"]) < OFFICIAL_RETRY_S:
            continue
        found = None
        for word in ("instrumental", "karaoke"):
            searches -= 1
            try:
                found = next((s for s in search(f"{fav['artist']} {fav['title']} {word}")
                              if is_instrumental_of(fav, s)), None)
            except Exception as e:  # noqa: BLE001
                log.info("music-bed favourites: search failed: %s", e)
                found = None
                break
            if found:
                break
        st["official_at"] = now
        st["official"] = found["id"] if found else ""
        if not found:
            continue
        fetches -= 1
        try:
            local = _fetch_local(found["id"])
        except Exception as e:  # noqa: BLE001
            log.info("music-bed favourites: fetching %s failed: %s", found["id"], e)
            local = None
        karaoke = not re.search(r"\binstrumental", found["title"], re.I)
        if local and Path(local).is_file() and _add(data, fav, Path(local), "official",
                                                     found["id"], karaoke):
            log.info("music-bed favourites: official instrumental of %s — %s (%s)",
                     fav["title"], fav["artist"], found["title"])
            got += 1
        save(data)
    return got


def host_ok() -> tuple[bool, str]:
    """Whether the host has room for Demucs now, and why not."""
    mem = _host_mem()
    stall = mem.get("mem_stall_pct")
    line = _num_env("MEDIA_MEM_STRAIN_PCT", DEFAULT_STRAIN_PCT)
    if stall is not None and stall >= line:
        return False, f"the host is short of memory (stalled {stall:g} % of the last minute)"
    avail = mem.get("mem_available_mb")
    if avail is not None and avail < min_free_mb():
        return False, f"only {avail} MB of memory free (needs {min_free_mb()} MB)"
    return True, ""


def demucs_cmd(exe: str, src: Path, out: Path) -> list[str]:
    """Demucs on `src` into `out`, niced, ioniced and (by default) under a
    memory ceiling with no swap."""
    core = [exe, "--two-stems", "vocals", "-n", model(), "-d", "cpu", "-j", "1",
            "-o", str(out), str(src)]
    wrapped = ["nice", "-n", "19"] + (["ionice", "-c", "3"] if shutil.which("ionice") else []) + core
    if guard() == "none":
        return wrapped
    return ["systemd-run", "--user", "--scope", "--quiet",
            "-p", f"MemoryMax={memory_max_mb()}M", "-p", "MemorySwapMax=0"] + wrapped


def _cut(src: Path, dst: Path) -> bool:
    """The original's first KEEP_S seconds as 44.1 kHz stereo wav, for
    Demucs (shorter is cheaper, and a bed never needs more)."""
    keep = int(_num_env("MEDIA_MUSIC_BED_FAVOURITES_KEEP_S", DEFAULT_KEEP_S))
    try:
        r = subprocess.run(["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
                            "-i", str(src), "-vn", "-t", str(keep), "-ar", "44100", "-ac", "2",
                            str(dst)], stdin=subprocess.DEVNULL, capture_output=True,
                           timeout=600)
        return r.returncode == 0 and dst.is_file() and dst.stat().st_size > 1024
    except (OSError, subprocess.SubprocessError):
        return False


def separate_one(fav: dict, local: Path) -> Optional[Path]:
    """Demucs's no_vocals stem of `local` (in the work dir), or None."""
    exe = demucs_exe()
    if not exe:
        return None
    work = _work_dir() / fav["key"]
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True, exist_ok=True)
    src = work / "original.wav"
    if not _cut(local, src):
        return None
    threads = str(max(1, int(_num_env("MEDIA_MUSIC_BED_FAVOURITES_THREADS", DEFAULT_THREADS))))
    env = dict(os.environ, OMP_NUM_THREADS=threads, MKL_NUM_THREADS=threads,
               OPENBLAS_NUM_THREADS=threads, TORCH_NUM_THREADS=threads)
    code = _run(demucs_cmd(exe, src, work / "out"), SEPARATE_TIMEOUT_S, env)
    stem = work / "out" / model() / "original" / "no_vocals.wav"
    if code != 0 or not stem.is_file():
        log.info("music-bed favourites: Demucs failed on %s — %s (exit %s)",
                 fav["title"], fav["artist"], code)
        return None
    return stem


def why_not_separate() -> Optional[str]:
    """Why vocals cannot be removed on this host at all, or None."""
    if not separate_on():
        return "Removing vocals is off on this server (MEDIA_MUSIC_BED_FAVOURITES_SEPARATE=0)."
    if not demucs_exe():
        return "Demucs is not installed on this server, so only official instrumentals are used."
    if guard() != "none" and not shutil.which("systemd-run"):
        return ("No memory guard for Demucs here (systemd-run not found); set "
                "MEDIA_MUSIC_BED_FAVOURITES_GUARD=none to run it without one.")
    return None


def separate(data: dict, when: Optional[datetime] = None, force_window: bool = False) -> int:
    """Remove the vocals from favourites with no official instrumental, in
    the nightly window, under the guard, up to the night's cap. How many
    came."""
    when = when or datetime.now()
    if why_not_separate() is not None:
        return 0
    if not force_window and not in_window(when):
        return 0
    night = night_of(when)
    data["nights"] = {k: v for k, v in data["nights"].items() if k >= night_of(
        when - timedelta(days=7))}
    got = 0
    while data["nights"].get(night, 0) < per_night():
        if cache_full(data):
            break
        # The untried first, then those that failed fewest times; none
        # twice in a night.
        due = [f for f in data["favourites"]
               if not _has_track(data, f["key"])
               and "official_at" in _state(data, f["key"])
               and not _state(data, f["key"]).get("official")
               and _state(data, f["key"]).get("fail", 0) < MAX_FAILURES
               and _state(data, f["key"]).get("night") != night]
        fav = min(due, key=lambda f: _state(data, f["key"]).get("fail", 0)) if due else None
        if fav is None:
            break
        ok, why = host_ok()
        if not ok:
            data["skipped"] = {"at": time.time(), "reason": why}
            save(data)
            log.info("music-bed favourites: not separating tonight: %s", why)
            break
        st = _state(data, fav["key"])
        st["night"] = night
        data["nights"][night] = data["nights"].get(night, 0) + 1
        save(data)
        stem = None
        try:
            local = _fetch_local(fav["id"])
        except Exception as e:  # noqa: BLE001
            log.info("music-bed favourites: fetching %s failed: %s", fav["id"], e)
            local = None
        try:
            if local and Path(local).is_file():
                t0 = time.monotonic()
                stem = separate_one(fav, Path(local))
                if stem is not None and _add(data, fav, stem, "separated"):
                    log.info("music-bed favourites: vocals removed from %s — %s in %.0f s",
                             fav["title"], fav["artist"], time.monotonic() - t0)
                    got += 1
                    st.pop("fail", None)
                else:
                    stem = None
        finally:
            shutil.rmtree(_work_dir() / fav["key"], ignore_errors=True)
        if stem is None:
            st["fail"] = st.get("fail", 0) + 1
        else:
            data.pop("skipped", None)
        save(data)
    return got


# ---- the background passes ---------------------------------------------------------------

def chosen() -> bool:
    """Whether the music bed's choice is this source (or PREPARE=always)."""
    if prepare_always():
        return True
    try:
        from . import music_bed
        parsed = music_bed.parse(music_bed.choice())
        return bool(parsed and parsed[0] == "favourites")
    except Exception:  # noqa: BLE001
        return False


def _due(data: dict, key: str, gap: float, now: float) -> bool:
    """Whether `data[key]` (a time) is missing or at least `gap` ago."""
    try:
        return key not in data or now - float(data[key]) >= gap
    except (TypeError, ValueError):
        return True


def needs(now: Optional[float] = None, when: Optional[datetime] = None) -> bool:
    """Whether the background process has work here: the favourites due,
    a pass due, or the window open with separations left tonight."""
    if not enabled():
        return False
    now = time.time() if now is None else now
    data = load()
    if _due(data, "favourites_at", FAVOURITES_TTL_S, now):
        return True
    if _due(data, "pass_at", PASS_GAP_S, now) and not cache_full(data):
        return True
    when = when or datetime.now()
    return (why_not_separate() is None and in_window(when)
            and data["nights"].get(night_of(when), 0) < per_night()
            and not cache_full(data))


def fill(now: Optional[float] = None, when: Optional[datetime] = None,
         force_window: bool = False) -> int:
    """The background pass: the favourites refreshed when due, official
    instrumentals looked for, and in the window vocals removed. One at a
    time (a lock). How many beds came."""
    if not enabled():
        return 0
    from . import _lock as fcntl
    lp = _lock_path()
    lp.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(lp), os.O_CREAT | os.O_RDWR, 0o644)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return 0
        now = time.time() if now is None else now
        data = load()
        if _due(data, "favourites_at", FAVOURITES_TTL_S, now):
            gather(data, now)
            save(data)
        got = 0
        if _due(data, "pass_at", PASS_GAP_S, now):
            got += official(data, now)
            data["pass_at"] = now
            save(data)
        got += separate(data, when, force_window)
        save(data)
        return got
    finally:
        os.close(fd)


# ---- what Settings is told -----------------------------------------------------------------

def note(style: str = "auto", when: Optional[datetime] = None) -> str:
    """A line for Settings: how many are ready, and when vocals are next
    removed (or why not, or why last night was skipped)."""
    data = load()
    n = counts(data)
    have = n["official"] + n["separated"] if style not in STYLES else n[style]
    parts = []
    of = f" of {len(data['favourites'])} favourites" if data["favourites"] else ""
    if have:
        parts.append(f"{have} ready{of} ({n['official']} official, {n['separated']} with "
                     f"the vocals removed)." if style not in STYLES else f"{have} ready{of}.")
    elif not data["favourites"] and data.get("favourites_at"):
        parts.append("No favourites found yet (likes, most played"
                     + (", ListenBrainz" if listenbrainz_user() else "") + ").")
    else:
        parts.append(f"None{of} ready yet; generated music plays until some are.")
    why = why_not_separate()
    if why:
        parts.append(why)
    elif style != "official":
        if cache_full(data):
            parts.append("The cache is full, so no more are made.")
        else:
            nxt = next_window(when)
            at = "now" if in_window(when) else nxt.strftime("%a %H:%M")
            parts.append(f"Vocals are removed {window_words()} (next: {at}), "
                         f"up to {per_night()} a night.")
        skipped = data.get("skipped") or {}
        if skipped and time.time() - float(skipped.get("at", 0)) < 86400:
            parts.append(f"Last skipped: {skipped.get('reason')}.")
    return " ".join(parts)


def main(argv: list[str]) -> int:
    cmd = argv[1] if len(argv) > 1 else "night"
    if cmd == "night":
        logging.basicConfig(level=logging.INFO, format="%(message)s")
        if not chosen():
            print("music-bed favourites: not the music bed's source; nothing to do "
                  "(MEDIA_MUSIC_BED_FAVOURITES_PREPARE=always prepares anyway)")
            return 0
        try:
            os.nice(10)
        except (OSError, AttributeError):
            pass
        print(f"music-bed favourites: {fill(force_window='--any-time' in argv)} new")
        return 0
    if cmd == "status":
        data = load()
        print(json.dumps({"counts": counts(data), "favourites": len(data["favourites"]),
                          "note": note(), "tracks": [credit(e) for e in tracks(None, data)]},
                         indent=1, ensure_ascii=False))
        return 0
    print("usage: python -m agent_media_core.music_bed_favourites night [--any-time] | status",
          file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
