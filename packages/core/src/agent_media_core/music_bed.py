"""The music bed: quiet generated music under a long reading.

Roadmap item 39 (David, 26 Oct 2026: "maybe we could even use dynamically
selected music in the background"; "calm and dusk are both nice … I'd also
like more"). Six moods, ported from the samples he liked
(docs/proposals/2026-10-26-music-bed-samples/, gen.py and gen2.py): an
electric piano over a slow pad, four seventh chords twice round.

- **calm** — Am7 Fmaj7 Cmaj7 G7, a bar of 4 s, four notes a bar.
- **dusk** — Dm7 Gm7 Cmaj7 Fmaj7, slower (4.8 s), three notes a bar.
- **blend** — calm's first two chords, dusk's last two (4.4 s).
- **morning** — Cmaj7 Fmaj7 Am7 G7, brighter and quicker (3.6 s).
- **rainy** — Am7 Fmaj7 Am7 Em7 (4.8 s) with soft rain behind it.
- **night** — open, low voicings, two notes a bar (5.6 s).

**When it plays** (intake/submit.py, `choose`): in place of the under tone
for a LONG reading — at least ``MEDIA_MUSIC_BED_MIN_S`` (45) seconds by the
device renderer's estimate (route/dj.py `estimate_seconds`), or at least
``MEDIA_MUSIC_BED_MIN_SENTENCES`` (12) sentences — and only when the
reading's own duck found nothing playing (no music, no book, no paused
players: `Coordinator.other_audio`). A thinking readout keeps `think`;
anything short keeps what it had. A streamed reply is judged on the
sentences it has when it starts (its length is not known yet), so with the
thresholds at 0 it gets a bed too. `media doc`, books and Listen play
through the book channel, not through submit, so they get none either.

**Its own bed for each kind** (David, 30 Oct 2026: "an alert or a person's
message should get different music"; "maybe the music can be alert
appropriate"): an agent's reply or a `media say` gets the Settings choice;
the other three always get a generated bed of their own (`KIND_BEDS`),
whatever the choice's source, and nothing when the choice is off:

- **people** — a person's message: open major chords in a higher, warmer
  register than any mood, in place of the harp.
- **question** — a question card: suspended chords that never resolve, one
  note a bar, quieter than the rest (-27 dBFS).
- **alert** — an alarm, reminder, timer or Home Assistant announcement: A
  minor to E, a steady four-to-the-bar pulse and nothing waiting, so it
  still sounds like something to look at.

``MEDIA_MUSIC_BED_KINDS`` (``people,question,alert``) lists which of the
three get theirs; ``none`` gives them back their tones.

**Which mood** (`mood_for`): MEDIA_MUSIC_BED / the Settings choice, else by
the local hour — morning 6–11, calm 11–14, blend 14–17, dusk 17–21, night
21–6 — and rainy whenever it is raining (drizzle, rain, showers, a storm)
from 6 to 21. The weather is Open-Meteo's current weather code, the source
astrotunes (the music-transit skill, radio_dj.py) uses: read from a cache
kept by the render process (`warm`), or from radio_dj's own cache, each at
most an hour old; never fetched while a reply waits. Where: the
the place set in Settings (personal_prefs.location), else
``MEDIA_MUSIC_BED_WEATHER`` "lat,lon"; unset, or ``off``, the hour alone
decides.

**Auto reads the moment from astrotunes** (David, 30 Oct 2026: "The music
picker should probably also use Astro Tunes"): the reading radio_dj.py
keeps (`radio_dj.moment`, in ``<state_dir>/radio-dj-context.json``, half an
hour) — six axes from 0 to 1, tempo, energy, warmth, brightness, lyric
density and texture, and the day's themes — when one at most half an hour
old is there. Only the background process (`warm`) asks astrotunes for a
new one; a reply only reads the file. ``MEDIA_MUSIC_BED_ASTRO=off`` stops
both. The mapping (`astro_auto`), first rule that holds:

- raining, by day → **rainy** (piano), as before;
- energetic and bright (energy and tempo averaging ≥ 0.6, brightness ≥ 0.5)
  → **morning** (lo-fi);
- dense texture (≥ 0.65) → **rainy** (ambient);
- low energy (< 0.4) and warm (≥ 0.6) → **dusk** (piano);
- low energy and cool → **night** (ambient);
- warm (≥ 0.6) → **blend** (jazz);
- otherwise → **calm** (piano).

Time of day stays a guardrail: from 21 to 6 nothing bright or energetic
(morning becomes dusk if warm, else night; lo-fi becomes piano), whatever the
axes say, as the music-transit skill's "time of day matters more". With no
fresh reading Auto is the hour and the weather, as above. Settings is told
why (`auto_why`: "dusk — low energy, warm (astrotunes)"). For favourites
the ready tracks are weighted by how near their tempo and energy (BPM and
loudness, measured once as each is made) are to the reading's.

**Slow only** (David, 30 Oct 2026: "I think the music should be restricted
to slower variations"), on unless MEDIA_MUSIC_BED_SLOW=0 or Settings'
"Slow music only" switch is off (`slow_on`; kept beside the choice): Auto
never picks a quick variation — energetic and bright gives **calm**
(piano), warm gives **blend** (bossa nova), and no genre is lo-fi or jazz;
by the hour morning is calm, blend's genre bossa nova and dusk's piano
(SLOW_GENRE_FOR_MOOD). A fixed lo-fi, jazz or morning plays its slow render
(SLOWED, `played`). A favourites, library or streaming track plays only
when measured slow (music_bed_tempo.py); with none, the generated stand-in.
Settings is told "— slow only" at the end of `auto_why`.

**The choice**: ``off``, ``auto`` (by time and weather) or a mood. Settings
stores one on the server (`<state_dir>/music-bed.json`, ``GET``/``POST
/speech/music-bed``), which wins; without one, ``MEDIA_MUSIC_BED`` (default
``auto``).

**Rendering** is pure Python and slow (about ten seconds a mood), so it never
happens while a reply waits: a reading that wants a bed whose file is not
there yet plays none and starts a detached ``python -m
agent_media_core.music_bed warm`` (one at a time, by a lock), which renders
every missing mood at low priority and refreshes the weather. Files are
versioned (``music-bed-<mood>-v<N>.wav``) in the speech clip dir, beside the
earcons, for the same reason they are there: it is the one place a phone's
player can fetch from. ``MEDIA_MUSIC_BED_RENDER=0`` stops the spawn (the
tests set it).

Each file is one seamless loop: the notes that ring past the loop's end are
mixed back in at its start (so the last sample leads straight into the
first), and rain is crossfaded across the seam. Mono, 48 kHz, peaking at
-28 dBFS: the samples' -24 and MEDIA_MUSIC_BED_GAIN_DB (-4; David, 30 Oct
2026: "a little bit quieter"), which also sets the kept tracks' level
(-34 LUFS) and Mopidy's (8). A gain other than -4 is in the file's name.
What loops under the voice fades in and out (under_fade.py).

**The sources** (David, 26 Oct 2026: "let's go with the three options for
music that you gave me earlier"; "it will be good to use some kind of online
library"), one Settings choice:

- **Generated** (this module and music_bed_styles.py): the six moods above
  and six genre styles — ambient, piano, lo-fi, jazz chords, bossa nova,
  nature — all
  rendered here. Auto picks a mood; a mood or style fixes it.
- **Your streaming music** (music_bed_stream.py): a quiet genre playlist
  (lo-fi, ambient, classical, jazz, piano, bossa nova) from YouTube Music
  through Mopidy,
  on the house speakers and red5 only; a phone says why not.
- **Online library** (music_bed_library.py): public-domain, CC0 and CC BY
  recordings from archive.org (and ccMixter), fetched in the background,
  normalised and played as clips, their credit shown in Settings.
- **Your favourites, instrumental** (music_bed_favourites.py; David, 30 Oct
  2026): his most liked and played tracks, as an official instrumental when
  YouTube Music has a confident one, else with the vocals removed by Demucs
  in a guarded nightly window; played as clips, at random. Styles: Auto
  (either), official only, separated only.

For the streaming and library sources Auto is a genre by the same hour and
weather (GENRE_FOR_MOOD); for favourites it is any ready track, and the
generated stand-in is Auto's mood. The choice is one string: ``off``, ``auto`` or a
generated style (the values from before there were sources), or
``<source>:<style>`` — ``streaming:jazz``, ``library:auto``,
``favourites:separated``.

**Fallback** (`choose`): the chosen source when it can play on the reading's
target and has something cached; else the generated style nearest it
(GENERATED_FOR; Auto's mood for Auto) once rendered; else the tone, as
before.

Nothing here raises into a reply.
"""

from __future__ import annotations

import array
import json
import logging
import math
import os
import random
import subprocess
import sys
import time
import wave
from datetime import datetime
from pathlib import Path
from typing import Callable, NamedTuple, Optional

log = logging.getLogger(__name__)

RATE = 48000
#: The generated beds' peak before MEDIA_MUSIC_BED_GAIN_DB (`peak_dbfs`).
BASE_PEAK_DBFS = -24.0
#: MEDIA_MUSIC_BED_GAIN_DB's default: every bed 4 dB under the levels it
#: had first (David, 30 Oct 2026: "a little bit quieter").
DEFAULT_GAIN_DB = -4.0
PEAK_DBFS = BASE_PEAK_DBFS + DEFAULT_GAIN_DB
#: Bumped whenever a recipe changes, so an old file is never reused. 2: the
#: slow versions, bossa nova, and 4 dB quieter (30 Oct 2026).
VERSION = 2

MOODS = ("calm", "dusk", "blend", "morning", "rainy", "night")
LABELS = {"calm": "Calm", "dusk": "Dusk", "blend": "Calm and dusk",
          "morning": "Morning", "rainy": "Rainy day", "night": "Late night"}
#: The generated genre styles (music_bed_styles.py), beside the moods.
GENRES = ("ambient", "piano", "lofi", "jazz", "bossa", "nature")
LABELS.update({"ambient": "Ambient", "piano": "Piano", "lofi": "Lo-fi",
               "jazz": "Jazz chords", "bossa": "Bossa nova",
               "nature": "Nature (rain, stream, birds)"})
#: Everything the generated source can play.
GENERATED = MOODS + GENRES
#: The beds under readings that are not an agent's reply (`wanted`'s kind):
#: always generated, never a Settings choice.
KIND_BEDS = ("people", "question", "alert")
LABELS.update({"people": "A person's message", "question": "A question",
               "alert": "An alert"})
#: The generated styles with a quick feel, each also rendered slower
#: (`<style>-slow`: longer bars, sparser notes, a much softer beat or none),
#: which is what plays while slow is on (`played`).
SLOWED = ("morning", "lofi", "jazz")
SLOW_IDS = tuple(f"{s}-slow" for s in SLOWED)
LABELS.update({f"{s}-slow": f"{LABELS[s]} (slow)" for s in SLOWED})
#: Everything rendered here.
RENDERED = GENERATED + SLOW_IDS + KIND_BEDS

SOURCES = ("generated", "streaming", "library", "favourites")
SOURCE_LABELS = {"generated": "Generated", "streaming": "Your streaming music",
                 "library": "Online library",
                 "favourites": "Your favourites, instrumental"}
#: The favourites source's styles beside Auto (music_bed_favourites.py).
FAV_STYLES = ("official", "separated")
FAV_LABELS = {"official": "Official instrumentals only",
              "separated": "Vocals removed only"}
#: The genres the streaming and library sources offer.
NET_GENRES = ("lofi", "ambient", "classical", "jazz", "piano", "bossa")
NET_LABELS = {"lofi": "Lo-fi", "ambient": "Ambient", "classical": "Classical",
              "jazz": "Jazz", "piano": "Piano", "bossa": "Bossa nova"}
#: The generated style that stands in for a genre with nothing cached.
GENERATED_FOR = {"lofi": "lofi", "ambient": "ambient", "classical": "piano",
                 "jazz": "jazz", "piano": "piano", "bossa": "bossa"}
#: Auto for the streaming and library sources: the genre for Auto's mood.
GENRE_FOR_MOOD = {"morning": "classical", "calm": "piano", "blend": "lofi",
                  "dusk": "jazz", "night": "ambient", "rainy": "piano"}
#: The same with slow on: no lo-fi or jazz; bossa nova for the warm blend.
SLOW_GENRE_FOR_MOOD = {**GENRE_FOR_MOOD, "blend": "bossa", "dusk": "piano"}
#: The moods Auto may pick with slow on (morning is quick: calm instead).
SLOW_MOODS = ("calm", "dusk", "night", "rainy", "blend")

AUTO_LABEL = "Auto (by time and weather)"

CHOICES = (("off", "auto") + GENERATED
           + tuple(f"{src}:{g}" for src in ("streaming", "library")
                   for g in ("auto",) + NET_GENRES)
           + tuple(f"favourites:{s}" for s in ("auto",) + FAV_STYLES))

#: Open-Meteo weather codes that are rain: drizzle, rain, showers, storms.
RAIN_CODES = frozenset({51, 53, 55, 56, 57, 61, 63, 65, 66, 67, 80, 81, 82,
                        95, 96, 99})
_RAIN_WORDS = ("drizzle", "rain", "shower", "thunder", "storm")
WEATHER_TTL_S = 3600.0

DEFAULT_MIN_S = 45.0
DEFAULT_MIN_SENTENCES = 12


# ---- the choice ---------------------------------------------------------------

def _settings_path() -> Path:
    from ._paths import state_dir
    return state_dir() / "music-bed.json"


def normalise(choice) -> Optional[str]:
    """`choice` in its one canonical spelling, or None when it is none:
    ``generated:dusk`` is ``dusk``, ``generated``/``generated:auto`` is
    ``auto``, ``library`` is ``library:auto``."""
    if not isinstance(choice, str):
        return None
    c = choice.strip().lower()
    src, sep, style = c.partition(":")
    if src == "generated":
        c = style if sep and style else "auto"
    elif src in ("streaming", "library", "favourites") and not sep:
        c = f"{src}:auto"
    return c if c in CHOICES else None


def compose(source: Optional[str], style: Optional[str] = None) -> Optional[str]:
    """The choice for a source ("off" too) and a style ("auto" when none)."""
    if source == "off":
        return "off"
    if source not in SOURCES:
        return None
    return normalise(f"{source}:{style or 'auto'}")


def parse(choice: Optional[str]) -> Optional[tuple[str, str]]:
    """(source, style) for a choice — style ``auto`` or an id — or None for
    off (and for nonsense)."""
    c = normalise(choice) if choice is not None else None
    if c is None or c == "off":
        return None
    if ":" in c:
        src, _, style = c.partition(":")
        return src, style
    return "generated", c


def _stored() -> dict:
    """Everything Settings saved: the choice, the slow switch, the level,
    the DJ mix's switches."""
    try:
        d = json.loads(_settings_path().read_text())
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _store(d: dict) -> None:
    p = _settings_path()
    # Anything saved but the stamp: the choice, slow, the level, the DJ
    # mix's switches (music_bed_mix).
    if not any(k != "at" for k in d):
        p.unlink(missing_ok=True)
        return
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(f".{p.name}.{os.getpid()}")
    tmp.write_text(json.dumps({**d, "at": time.time()}))
    os.replace(tmp, p)


def stored_choice() -> Optional[str]:
    """The Settings choice, or None when none was made."""
    return normalise(_stored().get("choice"))


def set_choice(choice: Optional[str]) -> Optional[str]:
    """Save the Settings choice (None forgets it, back to MEDIA_MUSIC_BED)."""
    d = _stored()
    if choice is None:
        d.pop("choice", None)
        _store(d)
        return None
    canon = normalise(choice)
    if canon is None:
        raise ValueError(f"no music bed choice {choice!r}")
    d["choice"] = canon
    _store(d)
    return canon


# ---- slow only ------------------------------------------------------------------

def stored_slow() -> Optional[bool]:
    """The Settings slow switch, or None when it was never set."""
    v = _stored().get("slow")
    return v if isinstance(v, bool) else None


def env_slow() -> bool:
    """MEDIA_MUSIC_BED_SLOW: on unless 0, off, false or no."""
    return (os.environ.get("MEDIA_MUSIC_BED_SLOW") or "").strip().lower() not in (
        "0", "off", "false", "no")


def slow_on() -> bool:
    """Slow only (David, 30 Oct 2026: "I think the music should be restricted
    to slower variations"): Settings' switch, else MEDIA_MUSIC_BED_SLOW (on)."""
    s = stored_slow()
    return env_slow() if s is None else s


def set_slow(value: Optional[bool]) -> Optional[bool]:
    """Save the slow switch (None forgets it, back to MEDIA_MUSIC_BED_SLOW)."""
    if value is not None and not isinstance(value, bool):
        raise ValueError(f"slow must be true, false or null, not {value!r}")
    d = _stored()
    if value is None:
        d.pop("slow", None)
    else:
        d["slow"] = value
    _store(d)
    return value


def played(style: str, slow: Optional[bool] = None) -> str:
    """The generated file id that plays for `style`: its slow version
    (`<style>-slow`) while slow is on and it has one, else itself."""
    if style in SLOWED and (slow_on() if slow is None else slow):
        return f"{style}-slow"
    return style


#: What GET /speech/music-bed lists as `settings`: switches beside the choice.
SLOW_LABEL = "Slow music only"
SLOW_HINT = "Only slow, quiet pieces: no beat-driven or quick ones."


def env_choice() -> str:
    """MEDIA_MUSIC_BED (off, auto, a style, or source:style), with
    MEDIA_MUSIC_BED_SOURCE (generated, streaming, library) choosing the
    source when MEDIA_MUSIC_BED names only a style, or auto."""
    raw = (os.environ.get("MEDIA_MUSIC_BED") or "").strip().lower()
    if raw in ("0", "false", "no", "none", "off"):
        return "off"
    if raw in ("1", "true", "yes", "on"):
        raw = ""
    src = (os.environ.get("MEDIA_MUSIC_BED_SOURCE") or "").strip().lower()
    if src in SOURCES and ":" not in raw:
        return compose(src, raw or "auto") or compose(src, "auto") or "auto"
    return normalise(raw) or "auto"


def choice() -> str:
    """off, auto, a style or source:style: Settings', else the env's (auto)."""
    return stored_choice() or env_choice()


# ---- the weather ----------------------------------------------------------------

def _weather_cache() -> Path:
    from ._paths import state_dir
    return state_dir() / "music-bed-weather.json"


def _latlon() -> Optional[tuple[float, float]]:
    """Where the weather is read: Settings' place, else the env's; None
    (no weather) when neither is set."""
    from . import personal_prefs
    return personal_prefs.latlon()


def is_rain(label: Optional[str]) -> bool:
    w = (label or "").lower()
    return any(word in w for word in _RAIN_WORDS)


def weather_label(now: Optional[float] = None) -> Optional[str]:
    """The current weather's words ("light rain"), or None when nothing at
    most an hour old is known. Reads caches only: never fetches."""
    if _latlon() is None:
        return None
    now = time.time() if now is None else now
    from ._paths import state_dir
    for path, get in ((_weather_cache(), lambda d: d.get("label")),
                      (state_dir() / "radio-dj-context.json",
                       lambda d: (d.get("moment") or {}).get("weather"))):
        try:
            data = json.loads(path.read_text())
            if now - float(data.get("at", 0)) <= WEATHER_TTL_S:
                label = get(data)
                if label:
                    return str(label)
        except (OSError, ValueError, TypeError, AttributeError):
            continue
    return None


def weather_stale(now: Optional[float] = None) -> bool:
    if _latlon() is None:
        return False
    now = time.time() if now is None else now
    try:
        data = json.loads(_weather_cache().read_text())
        return now - float(data.get("at", 0)) > WEATHER_TTL_S * 0.75
    except (OSError, ValueError, TypeError):
        return True


def refresh_weather(timeout: float = 8.0) -> Optional[str]:
    """Fetch the current weather from Open-Meteo and cache it. The render
    process's job, never a reply's."""
    where = _latlon()
    if where is None:
        return None
    import urllib.parse
    import urllib.request
    q = urllib.parse.urlencode({"latitude": where[0], "longitude": where[1],
                                "current": "weather_code", "timezone": "auto"})
    try:
        with urllib.request.urlopen(f"https://api.open-meteo.com/v1/forecast?{q}",
                                    timeout=timeout) as r:
            code = int(json.loads(r.read().decode())["current"]["weather_code"])
    except Exception as e:  # noqa: BLE001
        # Kept as a failure with no label, so an offline host is not asked
        # again by every long reply for the next three quarters of an hour.
        log.info("music-bed: weather fetch failed: %s", e)
        code, label = None, None
    else:
        label = "rain" if code in RAIN_CODES else f"code {code}"
    p = _weather_cache()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_name(f".{p.name}.{os.getpid()}")
        tmp.write_text(json.dumps({"at": time.time(), "code": code, "label": label}))
        os.replace(tmp, p)
    except OSError:
        pass
    return label


# ---- astrotunes' reading of the moment ---------------------------------------------

#: How old a reading may be (radio_dj's own: half an hour).
ASTRO_TTL_S = 1800.0


def astro_on() -> bool:
    return (os.environ.get("MEDIA_MUSIC_BED_ASTRO") or "").strip().lower() not in (
        "0", "off", "false", "no")


def _astro_path() -> Path:
    from ._paths import state_dir
    return state_dir() / "radio-dj-context.json"


def astro_reading(now: Optional[float] = None) -> Optional[dict]:
    """astrotunes' axes `{name: 0..1}` (tempo, energy, warmth, brightness,
    lyric_density, texture) from radio_dj's kept reading at most half an
    hour old, or None. Reads the file only: never runs astrotunes."""
    if not astro_on():
        return None
    now = time.time() if now is None else now
    try:
        kept = json.loads(_astro_path().read_text())
        if now - float(kept.get("at", 0)) > ASTRO_TTL_S:
            return None
        axes = (kept.get("moment") or {}).get("axes") or {}
        out = {}
        for k, v in axes.items():
            val = v.get("value") if isinstance(v, dict) else v
            if isinstance(val, (int, float)):
                out[k] = max(0.0, min(1.0, float(val)))
        return out if "energy" in out else None
    except (OSError, ValueError, TypeError, AttributeError):
        return None


def astro_stale(now: Optional[float] = None) -> bool:
    """Whether the background process should ask astrotunes again: on, the
    program there, and no reading or one older than three quarters of its
    life."""
    if not astro_on():
        return False
    try:
        # The personal settings' Astro Tunes switch, where there is one.
        from . import personal_prefs
        if not personal_prefs.astro_enabled():
            return False
    except (ImportError, AttributeError):
        pass
    except Exception:  # noqa: BLE001
        return False
    import shutil
    if not shutil.which(os.environ.get("MEDIA_RADIO_ASTROTUNES", "astrotunes")):
        return False
    now = time.time() if now is None else now
    try:
        kept = json.loads(_astro_path().read_text())
        return now - float(kept.get("at", 0)) > ASTRO_TTL_S * 0.75
    except (OSError, ValueError, TypeError):
        return True


def refresh_astro() -> None:
    """A new reading (radio_dj.moment runs astrotunes and keeps it). The
    background process's job, never a reply's."""
    try:
        from . import radio_dj
        radio_dj.moment()
    except Exception as e:  # noqa: BLE001
        log.info("music-bed: astrotunes: %s", e)


def is_late(hour: int) -> bool:
    """21 to 6: nothing bright or energetic, whatever the axes say."""
    return hour >= 21 or hour < 6


class Auto(NamedTuple):
    """What Auto picks now: the generated mood, the genre for the streaming
    and library sources, why, and the reading it went by (None: the clock)."""
    mood: str
    genre: str
    why: str
    axes: Optional[dict] = None


def astro_auto(axes: dict, hour: int, raining: bool = False,
               slow: Optional[bool] = None) -> Auto:
    """The mood and genre for astrotunes' axes at `hour` (the mapping in the
    module's docstring), with the words for why. `slow` (default
    `slow_on()`): never an energetic or quick one."""
    slow = slow_on() if slow is None else slow
    g = lambda k: float(axes.get(k, 0.5))  # noqa: E731
    energy, tempo, warmth = g("energy"), g("tempo"), g("warmth")
    bright, texture = g("brightness"), g("texture")
    lively = (energy + tempo) / 2
    if raining and not is_late(hour):
        mood, genre, words = "rainy", "piano", ["raining"]
    elif lively >= 0.6 and bright >= 0.5:
        mood, genre, words = "morning", "lofi", ["energetic", "bright"]
    elif texture >= 0.65:
        mood, genre, words = "rainy", "ambient", ["dense texture"]
    elif energy < 0.4 and warmth >= 0.6:
        mood, genre, words = "dusk", "piano", ["low energy", "warm"]
    elif energy < 0.4:
        mood, genre, words = "night", "ambient", ["low energy", "cool"]
    elif warmth >= 0.6:
        mood, genre, words = "blend", "jazz", ["steady", "warm"]
    else:
        mood, genre, words = "calm", "piano", ["steady"]
    if is_late(hour) and mood in ("morning", "calm", "blend"):
        mood = "dusk" if warmth >= 0.6 else "night"
        words.append("late, so kept low")
    if is_late(hour) and genre == "lofi":
        genre = "piano"
    if slow:
        if mood == "morning":
            # Energetic and bright: the brightest slow mood.
            mood = "calm"
            words.append("kept slow")
        if genre == "lofi":
            genre = "piano"
        elif genre == "jazz":
            genre = "bossa"
    return Auto(mood, genre, f"{mood} — {', '.join(words)} (astrotunes)", axes)


# ---- which mood -------------------------------------------------------------------

def mood_by_hour(hour: int) -> str:
    if 6 <= hour < 11:
        return "morning"
    if 11 <= hour < 14:
        return "calm"
    if 14 <= hour < 17:
        return "blend"
    if 17 <= hour < 21:
        return "dusk"
    return "night"


def auto_now(when: Optional[datetime] = None, weather: Optional[str] = "?",
             axes="?") -> Auto:
    """What Auto picks: by astrotunes' reading when a fresh one is kept
    (`axes` "?" reads it; None: the clock alone), else by the hour and the
    weather."""
    when = when or datetime.now()
    if weather == "?":
        weather = weather_label()
    if axes == "?":
        axes = astro_reading()
    slow = slow_on()
    if axes:
        return astro_auto(axes, when.hour, is_rain(weather), slow)
    genres = SLOW_GENRE_FOR_MOOD if slow else GENRE_FOR_MOOD
    mood = mood_by_hour(when.hour)
    if mood != "night" and is_rain(weather):
        return Auto("rainy", genres["rainy"], "rainy — it is raining")
    span = {"morning": "6–11", "calm": "11–14", "blend": "14–17", "dusk": "17–21",
            "night": "21–6"}[mood]
    if slow and mood not in SLOW_MOODS:
        return Auto("calm", genres[mood], f"calm — by the hour ({span}), kept slow")
    return Auto(mood, genres[mood], f"{mood} — by the hour ({span})")


def mood_for(when: Optional[datetime] = None, weather: Optional[str] = "?",
             pick: Optional[str] = None) -> Optional[str]:
    """The mood to play now, or None for off. `weather` "?" reads the cache;
    `pick` the choice (default: `choice()`)."""
    pick = choice() if pick is None else pick
    if pick == "off":
        return None
    if pick in GENERATED:
        return pick
    if ":" in pick:
        # Another source's choice: the mood its Auto goes by.
        pick = "auto"
    return auto_now(when, weather).mood


def _num_env(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, "") or default)
    except ValueError:
        return default


def is_long(est_s: Optional[float], n_sentences: int) -> bool:
    """A reading long enough for a bed: ≥ MEDIA_MUSIC_BED_MIN_S seconds
    estimated (45) or ≥ MEDIA_MUSIC_BED_MIN_SENTENCES sentences (12)."""
    if est_s is not None and est_s >= _num_env("MEDIA_MUSIC_BED_MIN_S", DEFAULT_MIN_S):
        return True
    return n_sentences >= _num_env("MEDIA_MUSIC_BED_MIN_SENTENCES", DEFAULT_MIN_SENTENCES)


class Pick(NamedTuple):
    """What a reading should have under it: the source, its style (never
    "auto": already decided) and the generated style that stands in."""
    source: str
    style: str
    fallback: str


def as_pick(p) -> Optional[Pick]:
    """A Pick, or a bare generated style (a mood) as one."""
    if p is None or isinstance(p, Pick):
        return p
    return Pick("generated", str(p), str(p))


def pick_for(pick: Optional[str] = None, when: Optional[datetime] = None,
             weather: Optional[str] = "?") -> Optional[Pick]:
    """The Pick for a choice (default: `choice()`), or None for off."""
    parsed = parse(choice() if pick is None else pick)
    if parsed is None:
        return None
    src, style = parsed
    auto = auto_now(when, weather)
    mood = auto.mood
    if src == "generated":
        s = mood if style == "auto" else style
        return Pick("generated", s, played(s))
    if src == "favourites":
        # Its style stays "auto" (any ready track) or a method; the stand-in
        # is Auto's mood.
        return Pick("favourites", style, played(mood or "calm"))
    genre = auto.genre if style == "auto" else style
    return Pick(src, genre, played(mood if style == "auto" and mood else GENERATED_FOR[genre]))


def kinds_on() -> tuple[str, ...]:
    """The KIND_BEDS that play (MEDIA_MUSIC_BED_KINDS, all three)."""
    raw = os.environ.get("MEDIA_MUSIC_BED_KINDS")
    if raw is None:
        return KIND_BEDS
    words = {w.strip().lower() for w in raw.split(",")}
    return tuple(k for k in KIND_BEDS if k in words)


def wanted(est_s: Optional[float], n_sentences: int,
           kind: str = "reply") -> Optional[Pick]:
    """What a reading of this length should have under it, if anything,
    before asking whether anything else is playing or a file is ready.
    `kind`: "reply" (an agent's reply or a say: the Settings choice), or one
    of KIND_BEDS (its own generated bed, while the choice is not off)."""
    try:
        if not is_long(est_s, n_sentences):
            return None
        pick = pick_for()
        if kind == "reply" or pick is None:
            return pick
        if kind not in kinds_on():
            return None
        return Pick("generated", kind, kind)
    except Exception as e:  # noqa: BLE001
        log.warning("music-bed: choosing failed: %s", e)
        return None


# ---- synthesis -----------------------------------------------------------------------

def _hz(m: int) -> float:
    return 440.0 * 2 ** ((m - 69) / 12)


#: A note's last 20 ms fade out. The samples cut each note dead at its
#: length — the bass at the bar line, still at 40 % — which is a click at
#: every bar (masked there by the next bar's attack) and so at the seam.
_RELEASE = int(RATE * 0.02)


def _epiano(f: float, d: float, dec: float = 1.6, bright: float = 0.25) -> list[float]:
    n = int(RATE * d)
    w = 2 * math.pi * f / RATE
    out = [min(1.0, i / (RATE * 0.008)) * math.exp(-dec * i / RATE)
           * (math.sin(w * i) + bright * math.sin(2 * w * i) * math.exp(-3 * i / RATE))
           for i in range(n)]
    r = min(_RELEASE, n)
    for k in range(r):
        out[n - r + k] *= 0.5 + 0.5 * math.cos(math.pi * (k + 1) / r)
    return out


def _pad(freqs, d: float, wob: float = 0.3) -> list[float]:
    n = int(RATE * d)
    ws = [2 * math.pi * f / RATE for f in freqs]
    lfo = 2 * math.pi * 0.4 / RATE
    k = len(freqs)
    return [math.sin(math.pi * i / n) ** 1.5
            * sum(math.sin(w * i + wob * math.sin(lfo * i + j)) for j, w in enumerate(ws)) / k
            for i in range(n)]


def _wrap_mix(dst: list[float], src: list[float], at_s: float, g: float) -> None:
    """`src` added into the loop `dst` from `at_s`, wrapping past its end to
    its start: what rings past the loop's end is heard as it comes round."""
    n = len(dst)
    o = int(RATE * at_s) % n
    for i, x in enumerate(src):
        dst[(o + i) % n] += g * x


def _rain(n: int, seed: int = 11) -> list[float]:
    rng = random.Random(seed)
    y = z = 0.0
    out = []
    for _ in range(n):
        y += 0.08 * (rng.uniform(-1, 1) - y)
        z += 0.02 * (y - z)
        out.append(y - z)
    return out


#: The recipes: (progression of (bass root, chord), bar seconds, notes a
#: bar, brightness, pad waver, melody note seconds, melody decay, jitter,
#: seed). calm and dusk as gen.py made them, the rest as gen2.py.
_RECIPES = {
    "calm": ([(57, [57, 60, 64, 67]), (53, [53, 57, 60, 64]),
              (48, [48, 52, 55, 59]), (55, [55, 59, 62, 65])],
             4.0, 4, 0.25, 0.3, 1.6, 1.6, 0.10, 5),
    "dusk": ([(50, [50, 53, 57, 60]), (55, [55, 58, 62, 65]),
              (48, [48, 52, 55, 59]), (53, [53, 57, 60, 64])],
             4.8, 3, 0.25, 0.3, 1.6, 1.6, 0.10, 5),
    "blend": ([(57, [57, 60, 64, 67]), (53, [53, 57, 60, 64]),
               (50, [50, 53, 57, 60]), (55, [55, 58, 62, 65])],
              4.4, 4, 0.25, 0.3, 1.8, 1.4, 0.12, 7),
    "morning": ([(60, [60, 64, 67, 71]), (65, [65, 69, 72, 76]),
                 (57, [57, 60, 64, 67]), (67, [67, 71, 74, 77])],
                3.6, 4, 0.4, 0.2, 1.8, 1.4, 0.12, 7),
    "rainy": ([(57, [57, 60, 64, 67]), (53, [53, 57, 60, 64]),
               (57, [57, 60, 64, 67]), (52, [52, 55, 59, 62])],
              4.8, 3, 0.2, 0.35, 1.8, 1.4, 0.12, 7),
    "night": ([(50, [50, 57, 60, 64]), (46, [46, 53, 57, 62]),
               (43, [43, 50, 53, 57]), (45, [45, 52, 55, 60])],
              5.6, 2, 0.12, 0.4, 1.8, 1.4, 0.12, 7),
    # The kind beds (KIND_BEDS). people: Fmaj9 G6 Em7 Am7, rootless and up
    # high, three long notes a bar.
    "people": ([(53, [57, 60, 64, 67]), (55, [59, 62, 64, 69]),
                (52, [55, 59, 62, 67]), (57, [60, 64, 67, 69])],
               4.2, 3, 0.35, 0.25, 2.0, 1.2, 0.15, 13),
    # question: Dsus4 Gsus2 Fsus2 Asus4, one note a bar, loosely placed.
    "question": ([(50, [50, 55, 57, 62]), (55, [55, 57, 62, 67]),
                  (53, [53, 55, 60, 65]), (57, [57, 62, 64, 69])],
                 5.0, 1, 0.15, 0.35, 2.6, 1.0, 0.3, 17),
    # alert: Am F Dm E, a bar of 2.4 s, four short bright notes on the beat.
    "alert": ([(57, [57, 60, 64, 69]), (53, [57, 60, 65, 69]),
               (50, [57, 62, 65, 69]), (52, [56, 59, 64, 68])],
              2.4, 4, 0.55, 0.15, 0.9, 2.4, 0.0, 19),
}
#: The slow versions of the quick moods (SLOWED), played while slow is on:
#: morning's chords in bars of 5 s, not 3.6, three notes a bar, not four,
#: and darker.
_SLOW_RECIPES = {
    "morning": ([(60, [60, 64, 67, 71]), (65, [65, 69, 72, 76]),
                 (57, [57, 60, 64, 67]), (67, [67, 71, 74, 77])],
                5.0, 3, 0.3, 0.3, 2.0, 1.2, 0.12, 7),
}
#: A recipe's peak against the rest's, where it is not the same.
_PEAK_OFFSETS = {"question": -3.0}


#: The range a stored level may take (dB against the first beds' levels).
GAIN_RANGE = (-24.0, 6.0)
#: How far under the voice the music sits at a stored level of 0: a kept
#: track is loudnormed to -30 LUFS there, and a reply's voice (Microsoft's
#: neural voices, raw or through the speaker's dynaudnorm) measures about
#: -20 LUFS (31 Oct 2026). Settings shows the level against the voice
#: (David, 31 Oct 2026: "what are the backing music levels relative to?";
#: he chose "Show it against the voice"): the stored -12 reads -22.
#: Approximate: songs and generated beds differ in loudness.
VOICE_REF_DB = 10.0
GAIN_LABEL = "Backing music level"
GAIN_HINT = ("How far under the voice the music sits, in dB: -22 is 22 dB quieter "
             "than the voice. Approximate, as songs differ. It also sets how far "
             "your own music drops while a reply plays.")


def shown_gain(stored: float) -> float:
    """A stored level as Settings shows it: against the voice."""
    return float(stored) - VOICE_REF_DB


def stored_from_shown(shown: float) -> float:
    """A level Settings sent (against the voice) as it is stored."""
    return float(shown) + VOICE_REF_DB


def stored_gain() -> Optional[float]:
    """The level Settings chose (POST /speech/music-bed settings `gain_db`),
    or None. Kept in the settings file, so every process reads the same one
    at once: an env value is fixed per process at its start, and a service
    started before a change kept the old level (David, 31 Oct 2026)."""
    try:
        v = _stored().get("gain_db")
        return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None
    except Exception:  # noqa: BLE001
        return None


def gain_db() -> float:
    """Every bed's level against the first ones' (-24 dBFS peak generated,
    -30 LUFS kept tracks, Mopidy at 12): Settings' `gain_db`, else
    MEDIA_MUSIC_BED_GAIN_DB, else -4."""
    g = stored_gain()
    return g if g is not None else _num_env("MEDIA_MUSIC_BED_GAIN_DB", DEFAULT_GAIN_DB)


def set_gain(value: Optional[float]) -> Optional[float]:
    """Save the level (None forgets it, back to MEDIA_MUSIC_BED_GAIN_DB)."""
    d = _stored()
    if value is None:
        d.pop("gain_db", None)
    else:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"gain_db must be a number of dB, not {value!r}")
        lo, hi = GAIN_RANGE
        if not lo <= float(value) <= hi:
            raise ValueError(f"gain_db must be between {lo:g} and {hi:g}")
        d["gain_db"] = float(value)
    _store(d)
    return value


def duck_scale() -> float:
    """How far the backing music level moves a reply's duck of David's own
    music (route/dj.py; David, 31 Oct 2026: "ducking follows the backing
    music slider"): the level's difference from the default as an amplitude
    ratio, 10 ** ((gain_db - DEFAULT_GAIN_DB) / 20). 1.0 at the default
    (-4), so every duck level is as it was; about 0.4 at -12. Approximate:
    tracks differ in loudness, so the same slider is not the same dB over
    every song. Never raises (1.0 on any failure)."""
    try:
        return 10 ** ((gain_db() - DEFAULT_GAIN_DB) / 20.0)
    except Exception:  # noqa: BLE001 — a duck must never cost a reply
        return 1.0


def peak_dbfs(mood: str = "") -> float:
    """A generated bed's peak: -24 dBFS and the gain (-28), the question's
    3 dB under the rest."""
    return BASE_PEAK_DBFS + gain_db() + _PEAK_OFFSETS.get(mood, 0.0)


def _split_slow(mood: str) -> tuple[str, bool]:
    if mood.endswith("-slow") and mood[:-5] in SLOWED:
        return mood[:-5], True
    return mood, False


def loop_seconds(mood: str) -> float:
    base, slow = _split_slow(mood)
    if base in GENRES:
        from . import music_bed_styles
        return music_bed_styles.loop_seconds(base, slow)
    recipe = _SLOW_RECIPES[base] if slow else _RECIPES[base]
    return 2 * len(recipe[0]) * recipe[1]


def render(mood: str) -> list[float]:
    """One seamless loop of `mood` (or genre style, or the slow version of
    either: `<id>-slow`), -1..1 before scaling."""
    base, slow = _split_slow(mood)
    if base in GENRES:
        from . import music_bed_styles
        return music_bed_styles.render(base, slow)
    recipe = _SLOW_RECIPES[base] if slow else _RECIPES[base]
    prog, bar, notes, bright, wob, mel_d, mel_dec, jit, seed = recipe
    rng = random.Random(seed)
    n = int(round(RATE * loop_seconds(mood)))
    s = [0.0] * n
    t = 0.0
    for _ in range(2):
        for root, ch in prog:
            _wrap_mix(s, _pad([_hz(m) for m in ch], bar + 0.8, wob), t, 0.5)
            _wrap_mix(s, _epiano(_hz(root - 12), bar, 0.9, bright), t, 0.35)
            for j in range(notes):
                m = rng.choice(ch) + 12
                _wrap_mix(s, _epiano(_hz(m), mel_d, mel_dec, bright),
                          t + j * bar / notes + rng.uniform(0, jit), 0.3)
            t += bar
    if mood == "rainy":
        # Rain one second longer than the loop, its tail crossfaded over its
        # head: the loop's last sample runs straight into its first.
        x = RATE
        r = _rain(n + x)
        for i in range(n):
            v = r[i]
            if i < x:
                a = 0.5 - 0.5 * math.cos(math.pi * i / x)
                v = a * r[i] + (1 - a) * r[n + i]
            s[i] += 0.6 * v
    return s


def synth(mood: str) -> bytes:
    """`mood` as 16-bit mono PCM at RATE, peaking at `peak_dbfs` (-28)."""
    samples = render(mood)
    peak = max((abs(v) for v in samples), default=0.0) or 1.0
    scale = (10 ** (peak_dbfs(mood) / 20.0)) * 32767 / peak
    pcm = array.array("h", (int(round(v * scale)) for v in samples))
    if sys.byteorder != "little":
        pcm.byteswap()
    return pcm.tobytes()


# ---- files -----------------------------------------------------------------------------

def _dir() -> Path:
    from .intake.submit import _audio_dir
    return _audio_dir()


def file_for(mood: str) -> Path:
    """The bed's file: versioned, and named for its gain when that is not
    the default, so a changed MEDIA_MUSIC_BED_GAIN_DB renders afresh."""
    g = gain_db()
    tag = "" if g == DEFAULT_GAIN_DB else f"-g{g:g}"
    return _dir() / f"music-bed-{mood}-v{VERSION}{tag}.wav"


def prune_old() -> int:
    """Delete the generated beds of other versions or gains (the clip dir
    keeps only what plays now), endings included. How many went."""
    keep = {file_for(m).name for m in RENDERED}
    try:
        from . import music_bed_outro
        keep |= music_bed_outro.names_kept()
    except Exception as e:  # noqa: BLE001
        log.info("music-bed: %s", e)
    n = 0
    try:
        for p in _dir().glob("music-bed-*-v*.wav"):
            if p.name not in keep:
                p.unlink(missing_ok=True)
                n += 1
    except OSError:
        pass
    return n


def rendered(mood: str) -> Optional[Path]:
    """The mood's file at the chosen level if it is rendered, else None."""
    try:
        p = file_for(mood)
        return p if p.is_file() and p.stat().st_size > 44 else None
    except OSError:
        return None


def ready(mood: str) -> Optional[Path]:
    """The mood's file at the chosen level, else (until the renderer catches
    up) its file at another level, played at the chosen one by the
    difference (music_bed_mix.level_offset); None with neither. Never
    renders."""
    p = rendered(mood)
    if p is not None:
        return p
    try:
        for q in sorted(_dir().glob(f"music-bed-{mood}-v{VERSION}*.wav")):
            if q.stat().st_size > 44:
                return q
    except OSError:
        pass
    return None


def write(mood: str) -> Path:
    """Render `mood` and write its file (atomically). Slow."""
    p = file_for(mood)
    p.parent.mkdir(parents=True, exist_ok=True)
    frames = synth(mood)
    tmp = p.with_name(f".{p.name}.{os.getpid()}")
    with wave.open(str(tmp), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(frames)
    os.replace(tmp, p)
    return p


def _render_off() -> bool:
    return (os.environ.get("MEDIA_MUSIC_BED_RENDER") or "").strip().lower() in (
        "0", "false", "no", "off")


def _lock_path() -> Path:
    from ._paths import state_dir
    return state_dir() / "music-bed-render.lock"


def _net_genres(style: str) -> tuple[str, ...]:
    return NET_GENRES if style == "auto" else (style,)


def warm_needed() -> bool:
    """Whether the background process has work: a generated style not yet
    rendered, the weather due, or the chosen library or streaming genre
    short of tracks (and not tried lately)."""
    if any(rendered(m) is None for m in RENDERED) or weather_stale() or astro_stale():
        return True
    try:
        from . import music_bed_outro
        if any(music_bed_outro.rendered(m) is None for m in music_bed_outro.styles()):
            return True
    except Exception as e:  # noqa: BLE001
        log.info("music-bed: %s", e)
    parsed = parse(choice())
    if parsed is None:
        return False
    src, style = parsed
    try:
        if src == "library":
            from . import music_bed_library as lib
            return lib.upkeep_due() or any(lib.needs(g) for g in _net_genres(style))
        if src == "streaming":
            from . import music_bed_stream as st
            return st.upkeep_due() or any(st.needs(g) for g in _net_genres(style))
        if src == "favourites":
            from . import music_bed_favourites as fav
            from . import music_bed_mix, music_bed_vocal
            return (fav.upkeep_due() or fav.needs()
                    or (music_bed_mix.on("vocal") and music_bed_vocal.due()))
    except Exception as e:  # noqa: BLE001
        log.info("music-bed: %s", e)
    return False


def spawn_warm() -> bool:
    """Start the detached render process unless it is off or not needed.
    True if one was started. Never raises."""
    if _render_off():
        return False
    try:
        if not warm_needed():
            return False
        subprocess.Popen([sys.executable, "-m", "agent_media_core.music_bed", "warm"],
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True)
        return True
    except Exception as e:  # noqa: BLE001
        log.info("music-bed: could not start the renderer: %s", e)
        return False


def warm() -> int:
    """Refresh the weather, render every missing generated mood and style
    (the one wanted now first), and fetch for the chosen library or
    streaming genre — one process at a time (a second one finds the lock
    held and leaves)."""
    from . import _lock as fcntl
    lp = _lock_path()
    lp.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(lp), os.O_CREAT | os.O_RDWR, 0o644)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return 0
        try:
            os.nice(19)
        except (OSError, AttributeError):
            pass
        if weather_stale():
            refresh_weather()
        if astro_stale():
            refresh_astro()
        done = 0
        first = pick_for()
        order = ([first.fallback] if first else []) + list(RENDERED)
        for style in dict.fromkeys(order):
            if rendered(style) is None:
                write(style)
                done += 1
        try:
            # Each bed's ending (music_bed_outro), after every bed.
            from . import music_bed_outro
            for style in music_bed_outro.styles():
                if music_bed_outro.rendered(style) is None:
                    music_bed_outro.write(style)
                    done += 1
        except Exception as e:  # noqa: BLE001
            log.warning("music-bed: rendering endings failed: %s", e)
        prune_old()
        parsed = parse(choice())
        if parsed:
            # The chosen source's kept tracks: measured for the slow rule
            # and brought to the bed's level, once each (music_bed_tempo).
            try:
                mod = {"library": "music_bed_library", "streaming": "music_bed_stream",
                       "favourites": "music_bed_favourites"}.get(parsed[0])
                if mod:
                    import importlib
                    importlib.import_module(f".{mod}", __package__).upkeep()
            except Exception as e:  # noqa: BLE001
                log.warning("music-bed: measuring kept tracks failed: %s", e)
        if parsed and parsed[0] == "favourites":
            try:
                from . import music_bed_favourites as fav
                done += fav.fill()
            except Exception as e:  # noqa: BLE001
                log.warning("music-bed: preparing favourites failed: %s", e)
            try:
                # The originals a long reply sings out into (music_bed_vocal).
                from . import music_bed_mix, music_bed_vocal
                if music_bed_mix.on("vocal"):
                    done += music_bed_vocal.upkeep()
            except Exception as e:  # noqa: BLE001
                log.warning("music-bed: keeping originals failed: %s", e)
        elif parsed and parsed[0] in ("library", "streaming"):
            src, style = parsed
            genres = _net_genres(style)
            if first is not None and first.style in genres:
                genres = (first.style,) + tuple(g for g in genres if g != first.style)
            try:
                if src == "library":
                    from . import music_bed_library as lib
                    done += lib.fill(genres)
                else:
                    from . import music_bed_stream as st
                    done += st.fill(genres)
            except Exception as e:  # noqa: BLE001
                log.warning("music-bed: fetching for %s failed: %s", src, e)
        return done
    finally:
        os.close(fd)


# ---- picking for a reading ------------------------------------------------------------------

def _target_name(target) -> Optional[str]:
    if target is None:
        return None
    return getattr(target, "name", None) or str(target)


def _source_ready(pick: Pick, target=None) -> bool:
    """Whether the chosen (not generated) source has something to play on
    `target` now. Never fetches."""
    try:
        if pick.source == "library":
            from . import music_bed_library as lib
            return bool(lib.playable(pick.style))
        if pick.source == "favourites":
            from . import music_bed_favourites as fav
            return fav.enabled() and bool(fav.playable(pick.style))
        if pick.source == "streaming":
            from . import music_bed_stream as st
            if target is not None and st.available(_target_name(target)) is not None:
                return False
            return bool(st.playable(pick.style))
    except Exception as e:  # noqa: BLE001
        log.info("music-bed: %s", e)
    return False


def ready_or_warm(pick, target=None):
    """Something to play for `pick` (a Pick or a mood): the chosen source's
    tracks or the generated file, else None (no bed this time). Starts the
    background process when the chosen source or the generated stand-in is
    not ready, or the weather is due. Never raises."""
    try:
        pick = as_pick(pick)
        if pick is None:
            return None
        src_ok = pick.source != "generated" and _source_ready(pick, target)
        p = ready(pick.fallback)
        # A file at another level plays now (its offset), but the renderer
        # is still due for the chosen level's.
        if (p is None or p.name != file_for(pick.fallback).name or weather_stale()
                or (pick.source != "generated" and not src_ok)):
            spawn_warm()
        return True if src_ok else p
    except Exception as e:  # noqa: BLE001
        log.warning("music-bed: %s", e)
        return None


class Plan(NamedTuple):
    """What to play under one reading: `kind` "under" (a clip the speech
    sink loops: a generated bed or a library track) or "stream" (tracks on
    Mopidy), what it is, and its credit for Settings."""
    kind: str
    source: str
    style: str
    path: Optional[Path] = None
    tracks: tuple = ()
    credit: Optional[dict] = None
    #: The generated style to play instead if the source fails as it starts.
    fallback: str = ""


def choose(pick, other_audio: Optional[bool], target=None) -> Optional[Plan]:
    """What to play under a reading, or None.

    In order: the chosen source, if it can play on `target` and has
    something cached; else the generated stand-in (`pick.fallback`), once
    rendered. `other_audio`: whether the reading's duck found something
    playing (None: not known, which counts as playing — a bed over someone's
    music is the failure to avoid). Never fetches, never renders, never
    raises."""
    pick = as_pick(pick)
    if pick is None or other_audio is not False:
        return None
    try:
        if pick.source == "library":
            from . import music_bed_library as lib
            got = lib.track_for(pick.style)
            if got is not None:
                return Plan("under", "library", pick.style, path=got[0], credit=got[1])
        elif pick.source == "favourites":
            from . import music_bed_favourites as fav
            got = fav.track_for(pick.style, astro_reading()) if fav.enabled() else None
            if got is not None:
                return Plan("under", "favourites", pick.style, path=got[0], credit=got[1])
        elif pick.source == "streaming":
            from . import music_bed_stream as st
            if st.available(_target_name(target)) is None:
                entries = st.pick(pick.style)
                if entries:
                    return Plan("stream", "streaming", pick.style, tracks=tuple(entries),
                                credit=st.credit(entries[0]), fallback=pick.fallback)
    except Exception as e:  # noqa: BLE001
        log.warning("music-bed: %s", e)
    p = ready(pick.fallback)
    if p is None:
        return None
    return Plan("under", "generated", pick.fallback, path=p)


def probe_wait_s() -> float:
    """How long a long reading waits for its duck to say whether anything is
    playing (MEDIA_MUSIC_BED_PROBE_S, 1 s). Past it, submit asks this host
    instead (Coordinator.audio_playing), without pausing anything."""
    return max(0.0, _num_env("MEDIA_MUSIC_BED_PROBE_S", 1.0))


def play_under(p: Path, target=None, sink=None, mix: Optional[dict] = None) -> bool:
    """Loop bed file `p` under the reading about to start on `target`, as
    earcons.play_under does a tone (SinkSpeech.play_under: a phone is told
    it as `under`, this host starts a second mpv that stop_under ends).
    False, and nothing played, without a sink, on a quiet phone or on any
    failure. Never raises."""
    if sink is None or target is None:
        return False
    under = getattr(sink, "play_under", None)
    if under is None:
        return False
    try:
        from .intake.submit import ringer_quiet
        if ringer_quiet(target) is not None:
            return False
        try:
            os.utime(p)
        except OSError:
            pass
        pre = getattr(sink, "prefetch", None)
        if pre is not None:
            extra = []
            if mix and mix.get("outro"):
                extra.append(Path(mix["outro"]))
            if mix and (mix.get("after") or {}).get("uri"):
                extra.append(Path(mix["after"]["uri"]))
            pre([p] + extra, target)
        if mix:
            # The DJ mix (music_bed_mix): told to a phone beside the clip,
            # ridden on this host's under player.
            try:
                ok = under(str(p), target, loop=True, mix=mix)
            except TypeError:
                ok = under(str(p), target, loop=True)
        else:
            ok = under(str(p), target, loop=True)
        if not ok:
            return False
        log.info("music-bed: %s under the reading on %s%s", p.name,
                 getattr(target, "name", target),
                 "" if not mix else " (mix: level %+.1f dB, swell %g, carve %g, intro %g ms%s)" % (
                     float(mix.get("gain_db") or 0) - float(mix.get("breathe_db") or 0),
                     mix.get("breathe_db") or 0, mix.get("carve_db") or 0,
                     mix.get("intro_ms") or 0, ", sings out" if mix.get("after") else ""))
        return True
    except Exception as e:  # noqa: BLE001 — a bed must never cost a reply
        log.warning("music-bed: playing failed: %s", e)
        return False


def _now_path() -> Path:
    from ._paths import state_dir
    return state_dir() / "music-bed-now.json"


def _note_now(plan: Optional[Plan], playing: bool) -> None:
    """What is (or was last) under a reading, for GET /speech/music-bed."""
    try:
        p = _now_path()
        if plan is None:
            data = json.loads(p.read_text())
            data["playing"] = playing
        else:
            data = {"source": plan.source, "style": plan.style,
                    "label": _style_label(plan.source, plan.style),
                    "credit": plan.credit, "playing": playing, "at": time.time()}
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_name(f".{p.name}.{os.getpid()}")
        tmp.write_text(json.dumps(data))
        os.replace(tmp, p)
    except (OSError, ValueError, TypeError):
        pass


def start(plan: Plan, target=None, sink=None,
          long: bool = False) -> tuple[bool, Optional[Callable[[], None]]]:
    """Start `plan` under the reading about to begin on `target`.

    Returns (looping under-player started, stop): the first says the speech
    sink's under player runs (its own stop_under ends it), the second is to
    be called when the reading is over (Mopidy's tracks taken back; the
    credit marked no longer playing). (False, None): nothing started. Never
    raises."""
    try:
        if plan.kind == "stream":
            from . import music_bed_stream as st
            stop = st.start(list(plan.tracks))
            if stop is None:
                # Mopidy busy or not answering: the generated stand-in.
                alt = plan.fallback or GENERATED_FOR.get(plan.style, "")
                p = ready(alt) if alt else None
                if p is None or not play_under(p, target, sink):
                    return False, None
                plan = Plan("under", "generated", alt, path=p)
                _note_now(plan, True)
                return True, lambda quick=False: _note_now(None, False)
            _note_now(plan, True)

            def done(quick: bool = False) -> None:
                try:
                    try:
                        stop(quick=quick)
                    except TypeError:
                        stop()
                finally:
                    _note_now(None, False)
            return False, done
        from . import music_bed_mix
        # `long`: a long reply (music_bed_vocal.is_long), which a favourite
        # sings out after.
        if plan.path is None or not play_under(plan.path, target, sink,
                                               music_bed_mix.for_plan(plan, long)):
            return False, None
        _note_now(plan, True)
        return True, lambda quick=False: _note_now(None, False)
    except Exception as e:  # noqa: BLE001 — a bed must never cost a reply
        log.warning("music-bed: starting failed: %s", e)
        return False, None


# ---- what Settings is told ----------------------------------------------------------------------

#: Auto for the favourites source: any ready track.
FAV_AUTO_LABEL = "Auto (any, at random)"


def _style_label(source: str, style: str) -> str:
    if style == "auto":
        return FAV_AUTO_LABEL if source == "favourites" else AUTO_LABEL
    if source == "generated":
        return LABELS.get(style, style)
    if source == "favourites":
        return FAV_LABELS.get(style, style)
    return NET_LABELS.get(style, style)


def _styles(source: str) -> list[str]:
    if source == "generated":
        return list(GENERATED)
    if source == "favourites":
        return list(FAV_STYLES)
    return list(NET_GENRES)


def _targets() -> list[dict]:
    try:
        from .audio_targets import speech_options
        return [{"name": o["name"], "label": o["label"]} for o in speech_options()]
    except Exception:  # noqa: BLE001
        return []


def _current_target() -> Optional[str]:
    try:
        from .audio_targets import speech_default
        return speech_default()
    except Exception:  # noqa: BLE001
        return None


def _why_not(source: str, target: Optional[str]) -> Optional[str]:
    """Why `source` cannot play under a reading on `target`, or None."""
    if source == "streaming":
        from . import music_bed_stream as st
        return st.available(target)
    if source == "library":
        from . import music_bed_library as lib
        if not lib.enabled():
            return "Switched off on this server (MEDIA_MUSIC_BED_LIBRARY=0)."
    if source == "favourites":
        from . import music_bed_favourites as fav
        if not fav.enabled():
            return "Switched off on this server (MEDIA_MUSIC_BED_FAVOURITES=0)."
    return None


def _note(source: str, current: Optional[tuple[str, str]]) -> Optional[str]:
    """A line about the chosen source's cache, for Settings."""
    if current is None or current[0] != source or source == "generated":
        return None
    if source == "favourites":
        try:
            from . import music_bed_favourites as fav
            return fav.note(current[1])
        except Exception:  # noqa: BLE001
            return None
    style = current[1]
    try:
        if source == "library":
            from . import music_bed_library as mod
        else:
            from . import music_bed_stream as mod
        n = mod.counts()
        slow = len(mod.playable(None if style == "auto" else style))
    except Exception:  # noqa: BLE001
        return None
    have = sum(n.values()) if style == "auto" else n.get(style, 0)
    if have:
        line = f"{have} track{'s' if have != 1 else ''} kept on the server."
        if slow_on():
            line += (f" {slow} slow enough to play (slow only)." if slow else
                     " None slow enough yet (slow only); generated music plays.")
        return line
    return ("Fetching a few tracks in the background; generated music plays "
            "until they are here.")


def settings() -> list[dict]:
    """The settings beside the choice, for an app to render as they come:
    `kind` "bool" (a checkbox with `label` and `hint`) or "range" (a slider:
    `min`, `max`, `step`, `unit`, its value a number); `set` is whether
    Settings chose rather than the server's default. The level, slow, and
    the DJ mix's switches (music_bed_mix)."""
    from . import music_bed_mix
    lo, hi = GAIN_RANGE
    # Against the voice (VOICE_REF_DB); stored, and used, against the first beds.
    return ([{"id": "gain_db", "kind": "range", "label": GAIN_LABEL, "hint": GAIN_HINT,
              "min": shown_gain(lo), "max": shown_gain(hi), "step": 1, "unit": "dB",
              "value": shown_gain(gain_db()), "set": stored_gain() is not None},
             {"id": "slow", "kind": "bool", "label": SLOW_LABEL, "hint": SLOW_HINT,
              "value": slow_on(), "set": stored_slow() is not None}]
            + music_bed_mix.settings())


def set_settings(values: dict) -> None:
    """Save `{id: value}` from POST /speech/music-bed's `settings` (null
    forgets one). ValueError for an unknown id or a bad value, before any
    is saved."""
    from . import music_bed_mix
    if not isinstance(values, dict) or not values:
        raise ValueError("settings must be an object of {id: value}")
    for k, v in values.items():
        if k == "slow":
            if v is not None and not isinstance(v, bool):
                raise ValueError("slow must be true, false or null")
        elif k == "gain_db":
            # Sent against the voice, as settings() shows it.
            lo, hi = shown_gain(GAIN_RANGE[0]), shown_gain(GAIN_RANGE[1])
            if v is not None and (isinstance(v, bool) or not isinstance(v, (int, float))
                                  or not lo <= float(v) <= hi):
                raise ValueError(f"gain_db must be a number from {lo:g} "
                                 f"to {hi:g}, or null")
        elif k in music_bed_mix.IDS:
            if v is not None and not isinstance(v, bool):
                raise ValueError(f"{k} must be true, false or null")
        else:
            raise ValueError(f"no music bed setting {k!r}")
    for k in music_bed_mix.IDS:
        if k in values:
            music_bed_mix.set_switch(k, values[k])
    if "slow" in values:
        set_slow(values["slow"])
    if "gain_db" in values:
        v = values["gain_db"]
        set_gain(None if v is None else stored_from_shown(v))
        # The files at the new level, rendered and re-levelled in the
        # background; until then each plays at the new level by its offset.
        spawn_warm()


def status() -> dict:
    """What `GET /speech/music-bed` answers, beside the choice."""
    stored = stored_choice()
    c = stored or env_choice()
    parsed = parse(c)
    targets = _targets()
    current = _current_target()
    sources = []
    for src in SOURCES:
        per = []
        for t in targets:
            why = _why_not(src, t["name"])
            per.append({"target": t["name"], "label": t["label"],
                        "available": why is None, "reason": why})
        why_here = _why_not(src, current)
        sources.append({
            "id": src, "label": SOURCE_LABELS[src],
            "styles": [{"id": "auto", "label": _style_label(src, "auto")}]
                      + [{"id": s, "label": _style_label(src, s)} for s in _styles(src)],
            "available": why_here is None, "reason": why_here,
            "targets": per, "note": _note(src, parsed)})
    flat = [{"id": "off", "label": "Off"}, {"id": "auto", "label": AUTO_LABEL}]
    flat += [{"id": s, "label": LABELS[s]} for s in GENERATED]
    for src in ("streaming", "library"):
        flat += [{"id": f"{src}:{s}",
                  "label": f"{SOURCE_LABELS[src]}: {_style_label(src, s)}"}
                 for s in ("auto",) + NET_GENRES]
    flat += [{"id": f"favourites:{s}",
              "label": f"{SOURCE_LABELS['favourites']}: {_style_label('favourites', s)}"}
             for s in ("auto",) + FAV_STYLES]
    auto_pick = pick_for(compose(parsed[0], "auto") if parsed else "auto")
    auto = auto_now()
    if parsed and parsed[0] == "favourites":
        auto_why = (("by fit to tempo and energy — " + auto.why) if auto.axes
                    else "a ready track at random")
    elif parsed and parsed[0] in ("streaming", "library"):
        auto_why = f"{auto.genre} — " + auto.why.split(" — ", 1)[1]
    else:
        auto_why = auto.why
    slow = slow_on()
    auto_why = f"auto: {auto_why}" + (" — slow only" if slow else "")
    try:
        now = json.loads(_now_path().read_text())
        if not isinstance(now, dict):
            now = None
    except (OSError, ValueError):
        now = None
    try:
        from . import music_bed_library as lib
        lib_counts = lib.counts()
    except Exception:  # noqa: BLE001
        lib_counts = {}
    try:
        from . import music_bed_stream as st
        st_counts = st.counts()
    except Exception:  # noqa: BLE001
        st_counts = {}
    try:
        from . import music_bed_favourites as fav
        fav_counts = fav.counts()
    except Exception:  # noqa: BLE001
        fav_counts = {}
    return {"choice": c, "set": stored is not None,
            "slow": slow,
            "settings": settings(),
            "source": parsed[0] if parsed else "off",
            "style": parsed[1] if parsed else None,
            "choices": flat,
            "sources": sources,
            "target": current,
            "auto_now": auto.mood,
            "auto_why": auto_why,
            "astro": auto.axes,
            "auto_style": (auto_pick.style if auto_pick and auto_pick.source != "favourites"
                           else None),
            "weather": weather_label(),
            "ready": [m for m in GENERATED if ready(m) is not None],
            "library": lib_counts, "streaming": st_counts,
            "favourites": fav_counts,
            "now": now,
            "credit": (now or {}).get("credit")}


def main(argv: list[str]) -> int:
    # The renderer reads the env file itself: it inherits whatever its
    # spawner had, and a service started before a setting changed (the
    # level, MEDIA_MUSIC_BED_GAIN_DB) rendered at the old one and pruned the
    # new files as another gain's (David, 31 Oct 2026: still not quieter).
    try:
        from .intake._env import load_env_file
        load_env_file("music-bed")
    except Exception as e:  # noqa: BLE001
        log.info("music-bed: env file not read: %s", e)
    cmd = argv[1] if len(argv) > 1 else "warm"
    if cmd == "warm":
        warm()
        return 0
    if cmd == "render" and len(argv) > 2 and argv[2] in RENDERED:
        print(write(argv[2]))
        return 0
    print("usage: python -m agent_media_core.music_bed warm | render <style>",
          file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
