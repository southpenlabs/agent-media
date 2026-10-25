"""The music bed from his own streaming music: a quiet genre playlist on Mopidy.

Roadmap item 39 (David, 26 Oct 2026: "let's go with the three options for
music that you gave me earlier"). The second source in Settings → Music
under long readings: lo-fi, ambient, classical, jazz or piano, found through
red5's music system — Mopidy and its YouTube Music backend (Mopidy-YouTube
with the music API) — and played by Mopidy into the house's music stream.

**Where it can play.** Mopidy's output is the rooms' Snapcast music stream
(`am-music`), the same place as the house speakers' speech and red5's own
(`local`), so only those targets get it (MEDIA_MUSIC_BED_STREAM_TARGETS,
default ``rooms,local``). A phone cannot, and says why in the server's list
(`PHONE_WHY`): the only way to his streaming music there is the radio's
hand-off player, which asks YouTube Music on the phone to play by name, and
that app takes the phone's audio focus as it starts — which pauses the
reply mid-sentence — and its volume is its own, so it cannot be held under
the voice. The phone plays generated or library music instead.

**How a track gets here.** red5 cannot stream from YouTube (its datacenter
address is bot-walled; sinks/music_fetch.py), so a track is fetched the way
every house play is: on the phone's residential address, copied into the
rooms cache (`music_fetch.ensure_local`). Like the library, that happens
only in music_bed's background process: a search (`search any`, the
genre's words in QUERIES) lists songs of 1.5 to 20 minutes and a few of
them (MEDIA_MUSIC_BED_STREAM_PER_STYLE, 3) are fetched; their names and
files are kept in `<state_dir>/music-bed-stream.json`. A genre that came up
short waits six hours to be tried again. Nothing cached means the generated
bed plays instead.

**Under the voice.** Only when Mopidy is stopped (a paused song is his and
is left alone) and the reading's duck found nothing else playing. The
tracks are added after whatever is in his queue, Mopidy's volume (and the
Mopidy-Mpv renderer's, through SinkMusic.duck) is faded up from nothing to
MEDIA_MUSIC_BED_STREAM_VOLUME (12 of 100) with the DJ duck's fader
(route/dj.py `fade`), and the first of them played. When the reading ends
it fades down, stops, takes its tracks back out of the queue and puts the
volume (and random) back as they were.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Callable, Optional

log = logging.getLogger(__name__)

GENRES = ("lofi", "ambient", "classical", "jazz", "piano")
LABELS = {"lofi": "Lo-fi", "ambient": "Ambient", "classical": "Classical",
          "jazz": "Jazz", "piano": "Piano"}
#: What Mopidy is asked for, per genre.
QUERIES = {"lofi": "lofi hip hop instrumental", "ambient": "ambient instrumental",
           "classical": "classical music instrumental", "jazz": "jazz instrumental",
           "piano": "solo piano instrumental"}

PHONE_WHY = ("Not on a phone: your streaming music would come from the phone's own "
             "music app (the radio's hand-off player), which takes the phone's audio "
             "when it starts, pausing the reply, and keeps its own volume, so it "
             "can't be held quietly under the voice. Generated music plays there "
             "instead.")

DEFAULT_PER_STYLE = 3
DEFAULT_RETRY_S = 6 * 3600.0
DEFAULT_VOLUME = 12
MIN_TRACK_S = 90
MAX_TRACK_S = 1200


def _num_env(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, "") or default)
    except ValueError:
        return default


def enabled() -> bool:
    return (os.environ.get("MEDIA_MUSIC_BED_STREAM") or "").strip().lower() not in (
        "0", "off", "false", "no")


def targets() -> tuple[str, ...]:
    raw = os.environ.get("MEDIA_MUSIC_BED_STREAM_TARGETS")
    if raw is None:
        return ("rooms", "local")
    return tuple(t.strip().lower() for t in raw.split(",") if t.strip())


def volume() -> int:
    return max(1, min(100, int(_num_env("MEDIA_MUSIC_BED_STREAM_VOLUME", DEFAULT_VOLUME))))


def per_style() -> int:
    return max(1, int(_num_env("MEDIA_MUSIC_BED_STREAM_PER_STYLE", DEFAULT_PER_STYLE)))


def _is_remote(name: str) -> bool:
    """A phone (a tcp:// player) or a target whose words are said on
    another host (a remote-say lane): Mopidy does not play there."""
    try:
        from .audio_targets import _lane
        if _lane(name):
            return True
        from .sinks.speech import _socket_for
        from .types import Target
        return str(_socket_for(Target(name=name))).startswith("tcp://")
    except Exception:  # noqa: BLE001
        return True


def available(name: Optional[str]) -> Optional[str]:
    """None when this source can play under a reading on target `name`,
    else why not (in words for the Settings list)."""
    if not enabled():
        return "Switched off on this server (MEDIA_MUSIC_BED_STREAM=0)."
    if not name:
        return "No speech target."
    if _is_remote(name):
        return PHONE_WHY
    if name not in targets():
        return ("Mopidy's music does not play where this target does "
                "(MEDIA_MUSIC_BED_STREAM_TARGETS).")
    return None


# ---- Mopidy (patched in the tests) --------------------------------------------------------

def _mpd(*lines: str) -> list[str]:
    from .sinks import music
    from .types import Target
    with music._connect(Target(name="local")) as s:
        return [music._cmd(s, line) for line in lines]


def _kv(text: str) -> dict:
    from .sinks import music
    return music._parse_kv(text)


def _set_volume(level: int) -> None:
    """Mopidy's volume and the Mopidy-Mpv renderer's (SinkMusic.duck does
    both: a `mpv:` file is played by the renderer, not by Mopidy's mixer)."""
    from .sinks.music import SinkMusic
    SinkMusic().duck(level=level)


def _fetch_local(vid: str) -> Optional[str]:
    """The track's file in the rooms cache, fetched on the phone if need be
    (sinks/music_fetch.py). Slow: the background process's job."""
    from .sinks import music_fetch
    return music_fetch.ensure_local(f"https://www.youtube.com/watch?v={vid}")


# ---- the index ------------------------------------------------------------------------------

def _index_path() -> Path:
    from ._paths import state_dir
    return state_dir() / "music-bed-stream.json"


def load() -> dict:
    try:
        data = json.loads(_index_path().read_text())
        if isinstance(data, dict):
            data.setdefault("tracks", [])
            data.setdefault("tried", {})
            return data
    except (OSError, ValueError):
        pass
    return {"tracks": [], "tried": {}}


def save(data: dict) -> None:
    p = _index_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(f".{p.name}.{os.getpid()}")
    tmp.write_text(json.dumps(data, indent=1))
    os.replace(tmp, p)


def tracks(genre: Optional[str] = None, data: Optional[dict] = None) -> list[dict]:
    data = load() if data is None else data
    out = []
    for e in data["tracks"]:
        if genre is not None and e.get("genre") != genre:
            continue
        f = e.get("file")
        if f and Path(f).is_file():
            out.append(e)
    return out


def counts() -> dict:
    data = load()
    return {g: len(tracks(g, data)) for g in GENRES}


def needs(genre: str, now: Optional[float] = None) -> bool:
    if not enabled() or genre not in GENRES:
        return False
    data = load()
    if len(tracks(genre, data)) >= per_style():
        return False
    now = time.time() if now is None else now
    return now - float(data["tried"].get(genre, 0)) >= DEFAULT_RETRY_S


def credit(entry: dict) -> dict:
    vid = entry.get("id") or ""
    return {"title": entry.get("title") or "", "creator": entry.get("artist") or "",
            "license": "", "license_url": "",
            "url": f"https://music.youtube.com/watch?v={vid}" if vid else "",
            "source": "YouTube Music"}


# ---- searching and fetching (background only) -----------------------------------------------

def search(genre: str) -> list[dict]:
    """Songs Mopidy's YouTube Music search finds for `genre`: `{id, title,
    artist, dur}`, 1.5 to 20 minutes long."""
    raw = _mpd(f'search any "{QUERIES[genre]}"')[0]
    out, cur = [], None
    for line in raw.splitlines():
        k, _, v = line.partition(": ")
        if k == "file":
            cur = {"file": v}
            out.append(cur)
        elif cur is not None:
            cur[k] = v
    songs = []
    for r in out:
        f = r.get("file", "")
        if not f.startswith("youtube:video:"):
            continue
        try:
            dur = int(float(r.get("Time") or 0))
        except ValueError:
            continue
        if not (MIN_TRACK_S <= dur <= MAX_TRACK_S):
            continue
        songs.append({"id": f.rsplit(":", 1)[1], "title": r.get("Title") or "",
                      "artist": r.get("Artist") or "", "dur": dur})
    return songs


def fill(genres, now: Optional[float] = None) -> int:
    got = 0
    for g in genres:
        if not needs(g, now):
            continue
        try:
            songs = search(g)
        except Exception as e:  # noqa: BLE001
            log.info("music-bed stream: search for %s failed: %s", g, e)
            songs = []
        data = load()
        have = {e.get("id") for e in data["tracks"]}
        want = per_style() - len(tracks(g, data))
        for s in songs:
            if want <= 0:
                break
            if s["id"] in have:
                continue
            try:
                local = _fetch_local(s["id"])
            except Exception as e:  # noqa: BLE001
                log.info("music-bed stream: fetching %s failed: %s", s["id"], e)
                local = None
            if not local:
                continue
            data = load()
            data["tracks"].append({**s, "genre": g, "file": local, "added": time.time()})
            # Only the index is ours to cap: the files are the rooms cache,
            # shared with every house play.
            data["tracks"] = data["tracks"][-len(GENRES) * per_style() * 2:]
            save(data)
            got += 1
            want -= 1
        data = load()
        data["tried"][g] = time.time() if now is None else now
        save(data)
    return got


def pick(genre: str) -> list[dict]:
    """The genre's cached tracks, the one played longest ago first."""
    have = tracks(genre)
    have.sort(key=lambda e: float(e.get("played", 0)))
    return have


# ---- playing under a reading ------------------------------------------------------------------

def start(entries: list[dict]) -> Optional[Callable[[], None]]:
    """Play `entries` on Mopidy, quietly, after his queue. The stop, or
    None when Mopidy is busy (playing or paused) or does not answer. Quick:
    the fade up runs on its own thread."""
    if not entries:
        return None
    try:
        status = _kv(_mpd("status")[0])
        if status.get("state", "stop") != "stop":
            return None
        before_vol = int(float(status.get("volume") or 100))
        if before_vol < 0:
            before_vol = 100
        before_random = status.get("random") == "1"
        first = int(status.get("playlistlength") or 0)
        uris = [f"mpv:{e['file']}" for e in entries]
        lines = ["random 0"] if before_random else []
        lines += [f'add "{u}"' for u in uris]
        _set_volume(0)
        _mpd(*lines)
        _mpd(f"play {first}")
    except Exception as e:  # noqa: BLE001 — a bed must never cost a reply
        log.info("music-bed stream: could not start: %s", e)
        return None
    data = load()
    for t in data["tracks"]:
        if t.get("id") == entries[0].get("id"):
            t["played"] = time.time()
    try:
        save(data)
    except OSError:
        pass
    level = volume()
    from .route import dj

    up = threading.Thread(target=lambda: _quiet(lambda: dj.fade(_set_volume, 0, level, 1500)),
                          daemon=True, name="music-bed-fade-up")
    up.start()
    done = threading.Event()

    def stop() -> None:
        if done.is_set():
            return
        done.set()
        up.join(timeout=3)
        _quiet(lambda: dj.fade(_set_volume, level, 0, 600))
        try:
            _mpd("stop")
            listing = _mpd("playlistinfo")[0]
            ours = set(uris)
            cur, mine = None, []
            for line in listing.splitlines():
                k, _, v = line.partition(": ")
                if k == "file":
                    cur = v
                elif k == "Pos" and v.isdigit():
                    pos = int(v)
                    if pos >= first and cur in ours:
                        mine.append(pos)
            for p in sorted(mine, reverse=True):
                _mpd(f"delete {p}")
            if before_random:
                _mpd("random 1")
        except Exception as e:  # noqa: BLE001
            log.info("music-bed stream: tidying up failed: %s", e)
        _quiet(lambda: _set_volume(before_vol))

    return stop


def _quiet(fn) -> None:
    try:
        fn()
    except Exception as e:  # noqa: BLE001
        log.debug("music-bed stream: %s", e)
