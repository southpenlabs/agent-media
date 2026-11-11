"""The listening mood: one setting that says what kind of music to play.

David, 11 Nov 2026: "Can we have some kind of a mood setting that also
dictates what kind of music is played ... including for backing music". Six
moods — auto, chill, focus, upbeat, sleep, party — chosen from chips on the
Media tab, Settings → Music, or by voice (``media music mood chill``). The
same day he found a "chilled" pick "a bit too lively, its base beat is a bit
fast for chill", so each mood is told to the DJ as a concrete tempo, beat,
energy and vocals, not a genre word.

**Auto** is what there was before: the DJ reads the moment (time, weather,
astrotunes) and the backing music's Auto goes by the hour, the weather and
the reading. Any other mood overrides that reading.

**Stored** on the server, ``<state_dir>/music-mood.json``; without one,
``MEDIA_MUSIC_MOOD`` (default ``auto``).

**The DJ** (radio_dj.py): the brief names the mood and what it means
(`dj_means`); the SYSTEM prompt says a chosen mood overrides the moment —
every pick must fit it, time and weather only shade the picks within it —
and the NOTE says it.

**The backing music** (music_bed.py `auto_now`): when the bed's choice is
Auto (or a source's Auto), the mood picks the bed mood and genre in place of
the hour, weather and astrotunes (`bed_auto`), and the favourites are
weighted by the mood's tempo and energy (`bed_axes`). "Slow music only"
still holds when on (party then gives blend/bossa, not morning/lo-fi). A
fixed bed choice in Settings still wins; so does Calm mode.

**Changing it** (`set_mood`, which the CLI and ``POST /music`` share):

- Unchanged: stored again, nothing else happens.
- A DJ station on (any new mood, auto too): the songs still to come — the
  list, and what was queued in the player behind the song playing — are
  dropped, as are their downloads, and the DJ is asked again at once
  (`radio.repick`; the server's loop does the asking within a pass), so the
  next song fits. The song playing plays on.
- A Mix station on, and a mood other than auto: a DJ station takes over on
  the same player (`radio.start_dj`): the song playing plays on until the
  DJ's first pick is ready. Back to auto leaves a Mix alone.
- No station, music playing (not paused), a mood other than auto: a DJ
  station starts on the default radio player, the same way.
- Nothing playing, or paused, or no station can play here (radio off on this
  server, no hand-off player): only stored. A station started later reads it.
- A station that cannot start (the player refuses one) leaves the mood set;
  the answer's ``station`` is null and the reason is logged.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Callable, NamedTuple, Optional

from ._paths import state_dir

log = logging.getLogger(__name__)


class Mood(NamedTuple):
    id: str
    label: str
    #: One line for the app's chips.
    blurb: str
    #: What the DJ is told it means (empty for auto).
    dj: str
    #: The backing music: generated bed mood, genre, and the same under
    #: "slow only" (music_bed.SLOW_MOODS / no lo-fi or jazz).
    bed: tuple[str, str]
    bed_slow: tuple[str, str]
    #: Tempo and energy (0-1) the favourites are weighted towards.
    axes: Optional[dict] = None


MOODS: tuple[Mood, ...] = (
    Mood("auto", "Auto", "Reads the moment: time of day, weather, your chart.",
         "", ("", ""), ("", "")),
    Mood("chill", "Chill", "Slow and soft: a gentle beat or none at all.",
         "Chill: slow songs, roughly 60-90 BPM, with soft or no drums and a light, "
         "unhurried bass; never a driving, punchy or four-on-the-floor beat. "
         "Downtempo, trip-hop, bossa nova, ambient pop, mellow acoustic, slow soul. "
         "A song that is mellow in genre but has a fast or busy beat does not fit.",
         ("dusk", "bossa"), ("dusk", "bossa"),
         {"tempo": 0.25, "energy": 0.25, "warmth": 0.7}),
    Mood("focus", "Focus", "Steady instrumentals to work to, few or no vocals.",
         "Focus: steady music to work to, mostly instrumental, no vocals or only "
         "sparse ones; an even tempo (roughly 70-110 BPM), low variation, no sudden "
         "drops, builds or attention-grabbing hooks. Ambient electronica, post-rock, "
         "modern classical, lo-fi beats, instrumental jazz, film scores.",
         ("calm", "piano"), ("calm", "piano"),
         {"tempo": 0.45, "energy": 0.4}),
    Mood("upbeat", "Upbeat", "Bright, feel-good songs with a lift.",
         "Upbeat: bright, feel-good songs with mid-to-high energy (roughly 100-130 "
         "BPM), mostly major keys, warm and happy rather than aggressive. Pop, funk, "
         "soul, indie pop, disco, feel-good classics.",
         ("blend", "jazz"), ("blend", "bossa"),
         {"tempo": 0.7, "energy": 0.7, "brightness": 0.75}),
    Mood("sleep", "Sleep", "Very slow and quiet: ambient, no drums, no words.",
         "Sleep: very slow and quiet music (under about 70 BPM): ambient, drones, "
         "soft piano or strings; no drums or beat at all, no vocals or only a "
         "near-whisper, nothing that builds or surprises. Each song as gentle as "
         "the one before.",
         ("night", "ambient"), ("night", "ambient"),
         {"tempo": 0.1, "energy": 0.1}),
    Mood("party", "Party", "Loud, danceable, high energy: the bangers.",
         "Party: loud, danceable, high-energy songs (roughly 115-130 BPM), "
         "well-known bangers and floor-fillers people sing along to. Dance-pop, "
         "house, disco, hip-hop and party classics; keep the energy up, no ballads.",
         ("morning", "lofi"), ("blend", "bossa"),
         {"tempo": 0.9, "energy": 0.9, "brightness": 0.8}),
)

BY_ID = {m.id: m for m in MOODS}
IDS = tuple(m.id for m in MOODS)
DEFAULT = "auto"


def _path() -> Path:
    return state_dir() / "music-mood.json"


def normalise(mood) -> Optional[str]:
    """A mood id, or None for one that is not."""
    if not isinstance(mood, str):
        return None
    m = mood.strip().lower()
    return m if m in BY_ID else None


def env_mood() -> str:
    return normalise(os.environ.get("MEDIA_MUSIC_MOOD") or "") or DEFAULT


def stored() -> Optional[str]:
    try:
        d = json.loads(_path().read_text())
    except (OSError, ValueError):
        return None
    return normalise(d.get("mood")) if isinstance(d, dict) else None


def current() -> str:
    """The mood now: the stored one, else MEDIA_MUSIC_MOOD, else auto."""
    return stored() or env_mood()


def get(mood: Optional[str] = None) -> Mood:
    return BY_ID[normalise(mood) or current()] if mood else BY_ID[current()]


def _store(mood: str) -> None:
    p = _path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps({"mood": mood}))
    os.replace(tmp, p)


def shown() -> dict:
    """For the music state (GET /music): the current id and the six, in order."""
    return {"id": current(),
            "moods": [{"id": m.id, "label": m.label, "blurb": m.blurb} for m in MOODS]}


# ---- what it means ----------------------------------------------------------------

def dj_means(mood: Optional[str] = None) -> Optional[dict]:
    """What the DJ's brief says of the mood; None for auto."""
    m = get(mood)
    if m.id == "auto":
        return None
    return {"id": m.id, "label": m.label, "means": m.dj}


def bed_auto(slow: bool, mood: Optional[str] = None) -> Optional[tuple[str, str, str]]:
    """The bed's (mood, genre, why) for the listening mood; None for auto."""
    m = get(mood)
    if m.id == "auto":
        return None
    bed, genre = m.bed_slow if slow else m.bed
    kept = " (kept slow)" if slow and m.bed_slow != m.bed else ""
    return bed, genre, f"{bed} — mood: {m.id}{kept}"


def bed_axes(mood: Optional[str] = None) -> Optional[dict]:
    """Tempo and energy the favourites are weighted towards; None for auto."""
    m = get(mood)
    return dict(m.axes) if m.id != "auto" and m.axes else None


# ---- changing it ------------------------------------------------------------------

def _playing_now() -> bool:
    """Whether music is playing (not paused), as `media music status` reads it."""
    try:
        from . import cli
        from .sinks.music import SinkMusic
        from .sinks.music_router import SinkMusicRouter

        out = cli._music_status_json(SinkMusicRouter(SinkMusic()))
    except Exception as e:  # noqa: BLE001 — unknown reads as not playing
        log.debug("music-mood: could not read the player: %s", e)
        return False
    return bool(out.get("uri") or out.get("path") or out.get("title")) and not out.get("paused")


def _default_where() -> str:
    from . import radio_io
    from .cli import _resolve_music_where

    return radio_io.default_player(_resolve_music_where("default"))


def _follow(mood: str, playing: Callable[[], bool]) -> Optional[str]:
    """The station after a change of mood: "repicked", "started" or None."""
    from . import radio

    st = radio.read()
    try:
        if st.get("on") and st.get("kind") == "dj":
            return "repicked" if radio.repick() else None
        if mood == "auto" or not radio.available():
            return None
        if st.get("on"):
            radio.start_dj(st.get("where") or _default_where())
            return "started"
        if playing():
            radio.start_dj(_default_where())
            return "started"
    except (ValueError, RuntimeError) as e:
        log.info("music-mood: no DJ station for %s: %s", mood, e)
    return None


def set_mood(mood, playing: Optional[Callable[[], bool]] = None) -> dict:
    """Set the mood, and bring the station in line (module docstring).
    `playing` says whether music plays now (default: the player is read).
    ``{"mood", "changed", "station"}``; ValueError for an unknown mood."""
    m = normalise(mood)
    if m is None:
        raise ValueError(f"mood must be one of {', '.join(IDS)}")
    before = current()
    _store(m)
    if m == before:
        return {"mood": m, "changed": False, "station": None}
    return {"mood": m, "changed": True, "station": _follow(m, playing or _playing_now)}
