"""The DJ: a radio station whose songs a model picks for the moment.

docs/proposals/2026-09-29-radio.md, "Claude DJ" (David, 29 Sep 2026, asked
how it should pick: "Mood + moment"). The station is agent_media_core.radio
with another source: where a Mix station lists YouTube's Mix, a DJ station
asks a model for the next few songs, and the phone finds each on YouTube.

**What it picks from.** The time of day, the weather where the listener
set their place (personal_prefs.py, Settings → Music → About you; none
unset), what the station has played, what was liked, and what was 👎'd.
With Astro Tunes switched on there (off by default), also the moment as
astrotunes reads it from their own chart: the transits turned into six
axes — tempo, energy, warmth, brightness, lyric density, texture — and the
day's themes (the music-transit skill's brief). The model is told that
reading only when "Let the DJ's model see the reading" is on too; never the
birth details. Reading the chart takes a few seconds, so it is kept half an
hour (`moment`, which the music bed's Auto reads too). Without it the DJ
goes by the clock and the weather.

**The model** is the follow-up's (MEDIA_FOLLOWUP_MODEL, a small hosted one)
through the summary gateway, unless MEDIA_RADIO_DJ_MODEL names another. It
answers with song lines, "Artist - Title", and one NOTE: line on the mood,
which the Media tab shows.

**Finding them.** One ssh to the phone, ``yt-dlp "ytsearch1:<line>" …``: the
first YouTube result for each line, listed and not downloaded, as the Mix is.
"""

from __future__ import annotations

import json
import logging
import os
import shlex
import subprocess
import time
from typing import Optional

from ._paths import state_dir

log = logging.getLogger(__name__)

#: Songs asked for at a time.
PICKS = 6
_CONTEXT_TTL_S = 1800

SYSTEM = (
    "You are the DJ of a personal radio station with one listener. Pick the "
    "next {n} songs to play, in order, for this moment. The brief gives the "
    "time of day, the weather when known, and sometimes a reading of the "
    "moment as six axes from 0 to 1 (tempo, energy, warmth, brightness, "
    "lyric density, texture); on an amplified axis do not go to extremes. "
    "Songs they liked show their taste; never pick an avoided song or artist, or "
    "one already played. Flow from what just played: no jarring jumps, and "
    "no more than two songs by one artist. Only real, well-known recordings "
    "you are sure exist, so a YouTube search finds them.\n\n"
    "Reply with exactly {n} lines, each 'Artist - Song title', nothing else "
    "on them, then one last line 'NOTE: ' and one short sentence on the mood "
    "you are going for."
)


def _context_path():
    return state_dir() / "radio-dj-context.json"


#: What of the moment is the astrotunes reading: told to a model only with
#: astro.to_model.
ASTRO_KEYS = ("axes", "posture", "novelty", "themes")


def _moment_key() -> str:
    """What the kept moment was read for: a changed setting reads again."""
    from . import personal_prefs as pp

    return json.dumps([pp.astro_enabled(), pp.latlon(), pp.natal_stdin() is not None])


def moment() -> dict:
    """The moment, kept half an hour: the local time, the weather where the
    listener set their place, and astrotunes' reading when Astro Tunes is on."""
    from . import personal_prefs as pp

    p = _context_path()
    key = _moment_key()
    try:
        kept = json.loads(p.read_text())
        if time.time() - kept.get("at", 0) < _CONTEXT_TTL_S and kept.get("key") == key:
            return kept["moment"]
    except (OSError, ValueError, KeyError):
        pass
    out: dict = {"local_time": time.strftime("%A %H:%M")}
    w = pp.weather_now()
    if w:
        out["weather"] = f"{w['label']}, {w['temperature_c']}°C" if w.get("temperature_c") is not None \
            else w["label"]
    ctx = pp.astro_context() if pp.astro_enabled() else None
    if ctx is None:
        if pp.astro_enabled():
            log.info("radio-dj: no astrotunes reading; the clock and the weather")
    else:
        out["time_of_day"] = ctx.get("time_of_day")
        q = ctx.get("qualities") or {}
        out["axes"] = {k: {"value": v.get("value"), "label": v.get("label"), "amplified": v.get("amplified")}
                       for k, v in (q.get("axes") or {}).items()}
        for k in ("posture", "novelty", "themes"):
            if q.get(k):
                out[k] = q[k]
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"at": time.time(), "key": key, "moment": out}))
    except OSError:
        pass
    return out


def told(m: dict) -> dict:
    """The moment as the model may see it: without the reading unless
    astro.to_model says so."""
    from . import personal_prefs as pp

    if pp.astro_to_model():
        return m
    return {k: v for k, v in m.items() if k not in ASTRO_KEYS}


def _likes() -> list[str]:
    try:
        from .state import StateStore

        return [r.get("title") for r in StateStore().list_likes(limit=15, channel="music") if r.get("title")]
    except Exception:  # noqa: BLE001
        return []


def brief(st: dict) -> str:
    """What the model is told: the moment, and the station so far."""
    titles = {s["id"]: f"{s.get('channel') or '?'} - {s.get('title')}" for s in st["sent"] + st["queue"]}
    played = [titles[v] for v in st["played"] if v in titles][-15:]
    banned = [titles[v] for v in st["banned"] if v in titles]
    struck = [c for c, n in st["strikes"].items() if n >= 2]
    return json.dumps({
        "moment": told(moment()),
        "just_played": played,
        "still_to_come": [titles[s["id"]] for s in st["queue"]][:5],
        "liked": _likes(),
        "avoid_songs": banned,
        "avoid_artists": struck,
    }, ensure_ascii=False, indent=1)


def ask(st: dict, n: int = PICKS) -> tuple[list[str], str]:
    """The model's next `n` lines, and its note on the mood."""
    from .intake._summary import DEFAULT_TIMEOUT, _chat, _int_env

    model = (os.environ.get("MEDIA_RADIO_DJ_MODEL") or os.environ.get("MEDIA_FOLLOWUP_MODEL") or None)
    out = _chat(SYSTEM.format(n=n), brief(st), _int_env("MEDIA_RADIO_DJ_TIMEOUT", max(DEFAULT_TIMEOUT, 150)),
                model=model) or ""
    lines, note = [], ""
    for line in out.splitlines():
        line = line.strip().lstrip("-*0123456789.) ").strip()
        if not line:
            continue
        if line.upper().startswith("NOTE:"):
            note = line[5:].strip()
        elif " - " in line and len(line) < 140:
            lines.append(line)
    if not lines:
        log.warning("radio-dj: the model picked nothing: %r", out[:300])
    return lines[:n], note


def search(lines: list[str]) -> list[dict]:
    """The first YouTube result for each line, found on the phone."""
    from . import radio_io
    from .sinks import music_local

    if not lines:
        return []
    urls = " ".join(shlex.quote(f"ytsearch1:{q}") for q in lines)
    remote = ("yt-dlp --no-warnings --flat-playlist --ignore-errors "
              f"--print '%(id)s\t%(title)s\t%(channel)s\t%(duration)s\t%(playlist)s' {urls}")
    try:
        r = subprocess.run(music_local.phone_argv(remote), capture_output=True, text=True,
                           timeout=float(os.environ.get("MEDIA_RADIO_MIX_TIMEOUT", "60")) + 10 * len(lines))
    except (subprocess.TimeoutExpired, OSError) as e:
        log.warning("radio-dj: searching failed: %s", e)
        return []
    return radio_io.parse(r.stdout or "")


def by_name(lines: list[str]) -> list[dict]:
    """The DJ's lines as songs for the hand-off player: the listener's music
    app finds each by name, so YouTube is not asked (licensed-music
    proposal); the id is the line's own."""
    from . import radio_io

    out = []
    for line in lines:
        artist, _, title = line.partition(" - ")
        out.append({"id": radio_io.synthetic_id(line), "title": title.strip() or line,
                    "channel": artist.strip() if title else "", "dur": None, "q": line})
    return out


def picks(st: dict, n: int = PICKS) -> tuple[list[dict], str]:
    from . import radio_io

    lines, note = ask(st, n)
    player = radio_io.PLAYERS.get(st.get("where") or "", radio_io.PhonePlayer)
    # Each song carries the DJ's line (q): what the hand-off player asks for.
    # Where the YouTube path is on, the lines are found there even for the
    # hand-off player, whose YouTube Music can then be asked by link.
    songs = search(lines) if getattr(player, "personal", False) or radio_io.youtube_on() \
        else by_name(lines)
    log.info("radio-dj: %d of %d found (%s)", len(songs), len(lines), note)
    return songs, note


def enabled() -> Optional[bool]:
    return os.environ.get("MEDIA_RADIO_DJ", "1") != "0"
