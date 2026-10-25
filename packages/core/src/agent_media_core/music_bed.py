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
players: `Coordinator.other_audio`). A thinking readout keeps `think`, a
person's message `people`, a question card, an alert and anything short keep
what they had. A streamed reply, whose length is not known when it starts,
gets no bed. `media doc`, books and Listen play through the book channel,
not through submit, so they get none either.

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
from typing import Optional

log = logging.getLogger(__name__)

RATE = 48000
PEAK_DBFS = -24.0
#: Bumped whenever a recipe changes, so an old file is never reused.
VERSION = 1

MOODS = ("calm", "dusk", "blend", "morning", "rainy", "night")
LABELS = {"calm": "Calm", "dusk": "Dusk", "blend": "Calm and dusk",
          "morning": "Morning", "rainy": "Rainy day", "night": "Late night"}
CHOICES = ("off", "auto") + MOODS

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


def stored_choice() -> Optional[str]:
    """The Settings choice, or None when none was made."""
    try:
        c = json.loads(_settings_path().read_text()).get("choice")
    except (OSError, ValueError, AttributeError):
        return None
    return c if c in CHOICES else None


def set_choice(choice: Optional[str]) -> Optional[str]:
    """Save the Settings choice (None forgets it, back to MEDIA_MUSIC_BED)."""
    p = _settings_path()
    if choice is None:
        p.unlink(missing_ok=True)
        return None
    if choice not in CHOICES:
        raise ValueError(f"no music bed choice {choice!r}")
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(f".{p.name}.{os.getpid()}")
    tmp.write_text(json.dumps({"choice": choice, "at": time.time()}))
    os.replace(tmp, p)
    return choice


def env_choice() -> str:
    raw = (os.environ.get("MEDIA_MUSIC_BED") or "").strip().lower()
    if raw in ("0", "false", "no", "none"):
        return "off"
    if raw in ("1", "true", "yes", "on", ""):
        return "auto"
    return raw if raw in CHOICES else "auto"


def choice() -> str:
    """off, auto or a mood: Settings', else MEDIA_MUSIC_BED's (auto)."""
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
    if pick in MOODS:
        return pick
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


def wanted(est_s: Optional[float], n_sentences: int) -> Optional[str]:
    """The mood a reading of this length should have under it, if any, before
    asking whether anything else is playing or the file is ready."""
    try:
        if not is_long(est_s, n_sentences):
            return None
        return mood_for()
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
}


def loop_seconds(mood: str) -> float:
    prog, bar = _RECIPES[mood][0], _RECIPES[mood][1]
    return 2 * len(prog) * bar


def render(mood: str) -> list[float]:
    """One seamless loop of `mood`, -1..1 before scaling."""
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
    """`mood` as 16-bit mono PCM at RATE, peaking at PEAK_DBFS."""
    samples = render(mood)
    peak = max((abs(v) for v in samples), default=0.0) or 1.0
    scale = (10 ** (PEAK_DBFS / 20.0)) * 32767 / peak
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


def warm_needed() -> bool:
    return any(ready(m) is None for m in MOODS) or weather_stale()


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
    """Refresh the weather and render every missing mood, one process at a
    time (a second one finds the lock held and leaves)."""
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
        for mood in MOODS:
            if ready(mood) is None:
                write(mood)
                done += 1
        return done
    finally:
        os.close(fd)


# ---- picking for a reading ------------------------------------------------------------------

def ready_or_warm(mood: str) -> Optional[Path]:
    """`mood`'s file if rendered; else start the renderer and None (no bed
    this time). Also starts it when the weather is due a refresh. Never
    raises."""
    try:
        p = ready(mood)
        if p is None or weather_stale():
            spawn_warm()
        return p
    except Exception as e:  # noqa: BLE001
        log.warning("music-bed: %s", e)
        return None


def choose(mood: Optional[str], other_audio: Optional[bool]) -> Optional[Path]:
    """The bed file to loop under a reading, or None.

    `other_audio`: whether the reading's duck found something playing (None:
    not known, which counts as playing — a bed over someone's music is the
    failure to avoid). Never renders, never raises."""
    if mood is None or other_audio is not False:
        return None
    return ready(mood)


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


def status() -> dict:
    """What `GET /speech/music-bed` answers, beside the choice."""
    stored = stored_choice()
    return {"choice": stored or env_choice(), "set": stored is not None,
            "choices": [{"id": c, "label": "Off" if c == "off" else
                         "Auto (by time and weather)" if c == "auto" else LABELS[c]}
                        for c in CHOICES],
            "auto_now": mood_for(pick="auto"),
            "weather": weather_label(),
            "ready": [m for m in MOODS if ready(m) is not None]}


def main(argv: list[str]) -> int:
    cmd = argv[1] if len(argv) > 1 else "warm"
    if cmd == "warm":
        warm()
        return 0
    if cmd == "render" and len(argv) > 2 and argv[2] in MOODS:
        print(write(argv[2]))
        return 0
    print("usage: python -m agent_media_core.music_bed warm | render <mood>",
          file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
