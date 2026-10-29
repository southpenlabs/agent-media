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
``MEDIA_MUSIC_BED_WEATHER`` "lat,lon" (Melbourne by default, as astrotunes);
``off`` stops the fetch, and the hour alone decides.

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
-24 dBFS, as the samples, so the voice stays on top.

**Three sources** (David, 26 Oct 2026: "let's go with the three options for
music that you gave me earlier"; "it will be good to use some kind of online
library"), one Settings choice:

- **Generated** (this module and music_bed_styles.py): the six moods above
  and five genre styles — ambient, piano, lo-fi, jazz chords, nature — all
  rendered here. Auto picks a mood; a mood or style fixes it.
- **Your streaming music** (music_bed_stream.py): a quiet genre playlist
  (lo-fi, ambient, classical, jazz, piano) from YouTube Music through Mopidy,
  on the house speakers and red5 only; a phone says why not.
- **Online library** (music_bed_library.py): public-domain, CC0 and CC BY
  recordings from archive.org (and ccMixter), fetched in the background,
  normalised and played as clips, their credit shown in Settings.

For the streaming and library sources Auto is a genre by the same hour and
weather (GENRE_FOR_MOOD). The choice is one string: ``off``, ``auto`` or a
generated style (the values from before there were sources), or
``<source>:<style>`` — ``streaming:jazz``, ``library:auto``.

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
PEAK_DBFS = -24.0
#: Bumped whenever a recipe changes, so an old file is never reused.
VERSION = 1

MOODS = ("calm", "dusk", "blend", "morning", "rainy", "night")
LABELS = {"calm": "Calm", "dusk": "Dusk", "blend": "Calm and dusk",
          "morning": "Morning", "rainy": "Rainy day", "night": "Late night"}
#: The generated genre styles (music_bed_styles.py), beside the moods.
GENRES = ("ambient", "piano", "lofi", "jazz", "nature")
LABELS.update({"ambient": "Ambient", "piano": "Piano", "lofi": "Lo-fi",
               "jazz": "Jazz chords", "nature": "Nature (rain, stream, birds)"})
#: Everything the generated source can play.
GENERATED = MOODS + GENRES
#: The beds under readings that are not an agent's reply (`wanted`'s kind):
#: always generated, never a Settings choice.
KIND_BEDS = ("people", "question", "alert")
LABELS.update({"people": "A person's message", "question": "A question",
               "alert": "An alert"})
#: Everything rendered here.
RENDERED = GENERATED + KIND_BEDS

SOURCES = ("generated", "streaming", "library")
SOURCE_LABELS = {"generated": "Generated", "streaming": "Your streaming music",
                 "library": "Online library"}
#: The genres the streaming and library sources offer.
NET_GENRES = ("lofi", "ambient", "classical", "jazz", "piano")
NET_LABELS = {"lofi": "Lo-fi", "ambient": "Ambient", "classical": "Classical",
              "jazz": "Jazz", "piano": "Piano"}
#: The generated style that stands in for a genre with nothing cached.
GENERATED_FOR = {"lofi": "lofi", "ambient": "ambient", "classical": "piano",
                 "jazz": "jazz", "piano": "piano"}
#: Auto for the streaming and library sources: the genre for Auto's mood.
GENRE_FOR_MOOD = {"morning": "classical", "calm": "piano", "blend": "lofi",
                  "dusk": "jazz", "night": "ambient", "rainy": "piano"}

AUTO_LABEL = "Auto (by time and weather)"

CHOICES = (("off", "auto") + GENERATED
           + tuple(f"{src}:{g}" for src in ("streaming", "library")
                   for g in ("auto",) + NET_GENRES))

#: Melbourne, as astrotunes.
DEFAULT_LATLON = (-37.8136, 144.9631)
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
    elif src in ("streaming", "library") and not sep:
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


def stored_choice() -> Optional[str]:
    """The Settings choice, or None when none was made."""
    try:
        c = json.loads(_settings_path().read_text()).get("choice")
    except (OSError, ValueError, AttributeError):
        return None
    return normalise(c)


def set_choice(choice: Optional[str]) -> Optional[str]:
    """Save the Settings choice (None forgets it, back to MEDIA_MUSIC_BED)."""
    p = _settings_path()
    if choice is None:
        p.unlink(missing_ok=True)
        return None
    canon = normalise(choice)
    if canon is None:
        raise ValueError(f"no music bed choice {choice!r}")
    choice = canon
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(f".{p.name}.{os.getpid()}")
    tmp.write_text(json.dumps({"choice": choice, "at": time.time()}))
    os.replace(tmp, p)
    return choice


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
    raw = (os.environ.get("MEDIA_MUSIC_BED_WEATHER") or "").strip().lower()
    if raw in ("off", "0", "no", "false", "none"):
        return None
    if raw:
        try:
            lat, lon = (float(x) for x in raw.split(","))
            return lat, lon
        except ValueError:
            log.info("music-bed: MEDIA_MUSIC_BED_WEATHER %r is not lat,lon", raw)
            return None
    return DEFAULT_LATLON


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
    when = when or datetime.now()
    mood = mood_by_hour(when.hour)
    if weather == "?":
        weather = weather_label()
    if mood != "night" and is_rain(weather):
        return "rainy"
    return mood


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
    mood = mood_for(when, weather, "auto")
    if src == "generated":
        s = mood if style == "auto" else style
        return Pick("generated", s, s)
    genre = GENRE_FOR_MOOD.get(mood or "calm", "piano") if style == "auto" else style
    return Pick(src, genre, mood if style == "auto" and mood else GENERATED_FOR[genre])


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
#: A recipe's own peak, where it is not PEAK_DBFS.
_PEAKS = {"question": -27.0}


def loop_seconds(mood: str) -> float:
    if mood in GENRES:
        from . import music_bed_styles
        return music_bed_styles.loop_seconds(mood)
    prog, bar = _RECIPES[mood][0], _RECIPES[mood][1]
    return 2 * len(prog) * bar


def render(mood: str) -> list[float]:
    """One seamless loop of `mood` (or genre style), -1..1 before scaling."""
    if mood in GENRES:
        from . import music_bed_styles
        return music_bed_styles.render(mood)
    prog, bar, notes, bright, wob, mel_d, mel_dec, jit, seed = _RECIPES[mood]
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
    """`mood` as 16-bit mono PCM at RATE, peaking at PEAK_DBFS (or its own,
    _PEAKS)."""
    samples = render(mood)
    peak = max((abs(v) for v in samples), default=0.0) or 1.0
    scale = (10 ** (_PEAKS.get(mood, PEAK_DBFS) / 20.0)) * 32767 / peak
    pcm = array.array("h", (int(round(v * scale)) for v in samples))
    if sys.byteorder != "little":
        pcm.byteswap()
    return pcm.tobytes()


# ---- files -----------------------------------------------------------------------------

def _dir() -> Path:
    from .intake.submit import _audio_dir
    return _audio_dir()


def file_for(mood: str) -> Path:
    return _dir() / f"music-bed-{mood}-v{VERSION}.wav"


def ready(mood: str) -> Optional[Path]:
    """The mood's file if it is rendered, else None. Never renders."""
    try:
        p = file_for(mood)
        return p if p.is_file() and p.stat().st_size > 44 else None
    except OSError:
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
    if any(ready(m) is None for m in RENDERED) or weather_stale():
        return True
    parsed = parse(choice())
    if parsed is None:
        return False
    src, style = parsed
    try:
        if src == "library":
            from . import music_bed_library as lib
            return any(lib.needs(g) for g in _net_genres(style))
        if src == "streaming":
            from . import music_bed_stream as st
            return any(st.needs(g) for g in _net_genres(style))
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
        done = 0
        first = pick_for()
        order = ([first.fallback] if first else []) + list(RENDERED)
        for style in dict.fromkeys(order):
            if ready(style) is None:
                write(style)
                done += 1
        parsed = parse(choice())
        if parsed and parsed[0] in ("library", "streaming"):
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
            return bool(lib.tracks(pick.style))
        if pick.source == "streaming":
            from . import music_bed_stream as st
            if target is not None and st.available(_target_name(target)) is not None:
                return False
            return bool(st.tracks(pick.style))
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
        if (p is None or weather_stale()
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
    playing (MEDIA_MUSIC_BED_PROBE_S, 1 s). Past it: no bed this time."""
    return max(0.0, _num_env("MEDIA_MUSIC_BED_PROBE_S", 1.0))


def play_under(p: Path, target=None, sink=None) -> bool:
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
            pre([p], target)
        if not under(str(p), target, loop=True):
            return False
        log.info("music-bed: %s under the reading on %s", p.name,
                 getattr(target, "name", target))
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


def start(plan: Plan, target=None, sink=None) -> tuple[bool, Optional[Callable[[], None]]]:
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
                return True, lambda: _note_now(None, False)
            _note_now(plan, True)

            def done() -> None:
                try:
                    stop()
                finally:
                    _note_now(None, False)
            return False, done
        if plan.path is None or not play_under(plan.path, target, sink):
            return False, None
        _note_now(plan, True)
        return True, lambda: _note_now(None, False)
    except Exception as e:  # noqa: BLE001 — a bed must never cost a reply
        log.warning("music-bed: starting failed: %s", e)
        return False, None


# ---- what Settings is told ----------------------------------------------------------------------

def _style_label(source: str, style: str) -> str:
    if style == "auto":
        return AUTO_LABEL
    if source == "generated":
        return LABELS.get(style, style)
    return NET_LABELS.get(style, style)


def _styles(source: str) -> list[str]:
    return list(GENERATED) if source == "generated" else list(NET_GENRES)


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
    return None


def _note(source: str, current: Optional[tuple[str, str]]) -> Optional[str]:
    """A line about the chosen source's cache, for Settings."""
    if current is None or current[0] != source or source == "generated":
        return None
    try:
        if source == "library":
            from . import music_bed_library as lib
            n = lib.counts()
        else:
            from . import music_bed_stream as st
            n = st.counts()
    except Exception:  # noqa: BLE001
        return None
    style = current[1]
    have = sum(n.values()) if style == "auto" else n.get(style, 0)
    if have:
        return f"{have} track{'s' if have != 1 else ''} kept on the server."
    return ("Fetching a few tracks in the background; generated music plays "
            "until they are here.")


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
            "styles": [{"id": "auto", "label": AUTO_LABEL}]
                      + [{"id": s, "label": _style_label(src, s)} for s in _styles(src)],
            "available": why_here is None, "reason": why_here,
            "targets": per, "note": _note(src, parsed)})
    flat = [{"id": "off", "label": "Off"}, {"id": "auto", "label": AUTO_LABEL}]
    flat += [{"id": s, "label": LABELS[s]} for s in GENERATED]
    for src in ("streaming", "library"):
        flat += [{"id": f"{src}:{s}",
                  "label": f"{SOURCE_LABELS[src]}: {_style_label(src, s)}"}
                 for s in ("auto",) + NET_GENRES]
    auto_pick = pick_for(compose(parsed[0], "auto") if parsed else "auto")
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
    return {"choice": c, "set": stored is not None,
            "source": parsed[0] if parsed else "off",
            "style": parsed[1] if parsed else None,
            "choices": flat,
            "sources": sources,
            "target": current,
            "auto_now": mood_for(pick="auto"),
            "auto_style": auto_pick.style if auto_pick else None,
            "weather": weather_label(),
            "ready": [m for m in GENERATED if ready(m) is not None],
            "library": lib_counts, "streaming": st_counts,
            "now": now,
            "credit": (now or {}).get("credit")}


def main(argv: list[str]) -> int:
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
