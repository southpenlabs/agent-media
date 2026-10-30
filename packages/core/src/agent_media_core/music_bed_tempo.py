"""Is a track slow? The music bed's "slow only" rule for real recordings.

Roadmap item 39 (David, 30 Oct 2026: "I think the music should be
restricted to slower variations"). With slow on (music_bed.slow_on,
MEDIA_MUSIC_BED_SLOW, default on, or the Settings switch), a favourites,
library or streaming track plays under a reading only when it is measured
slow. Each is measured once (`measure`): as it is made or fetched, and for
one kept from before, by the background process (music_bed.warm).

**What is measured** (from 90 s of the track, 15 s in, past an intro):

- **bpm** — the lag of the strongest autocorrelation of the onset envelope
  between 60 and 180 BPM (`bpm_of`). It is often an octave out: a nocturne
  or an ambient piece read at 160-180, the beat found at double the tempo;
  a busy lo-fi loop read at 70, half of it.
- **onsets** — how many notes or hits start a second (`onset_rate`): rises
  of at least +4 dB within 20 ms in the level, at least 100 ms apart. Calm
  pieces measured 0.7-1.8 a second, busy ones (lo-fi, swing, pop) 3-4.
- **energy** — the loudness before normalising, -20 LUFS → 0 to -8 → 1
  (raw, so a loud master stays loud). Compressed rock has few clear rises
  but is loud.

**The rule** (`is_slow`): a track is slow only when the tempo and the
busyness agree.

- *Busyness* says slow when there are at most MEDIA_MUSIC_BED_MAX_ONSETS
  (2.5) onsets a second, and, where the loudness is known, its energy is at
  most 0.85 (about -9.8 LUFS before normalising).
- *Tempo* says slow when the BPM is at most MEDIA_MUSIC_BED_MAX_BPM (95);
  or, for a reading above it whose half is under it (the doubled estimate),
  when the onsets are sparse too: at most 0.8 of the onset limit (2.0).
- A reading under the limit that is really half a quick tempo is caught by
  the busyness: a lo-fi loop read at 70 BPM has 4 onsets a second.
- A track never measured (or that ffmpeg could not read) is not slow.

The tests feed `bpm_of` and `onset_rate` envelopes directly; `measure` runs
ffmpeg and is patched there.
"""

from __future__ import annotations

import array
import logging
import math
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Callable, Iterable, Optional

log = logging.getLogger(__name__)

DEFAULT_MAX_BPM = 95.0
DEFAULT_MAX_ONSETS = 2.5
#: The share of the onset limit a doubled BPM reading must be under.
DOUBLED_ONSETS = 0.8
#: The most energy (raw loudness, 0..1) a slow track may have.
MAX_ENERGY = 0.85
#: Envelope frames a second.
RATE = 100
#: An onset: the level rising this much (natural log, about +4 dB) in 20 ms.
RISE = 0.5
GAP_S = 0.1


def _num_env(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, "") or default)
    except ValueError:
        return default


def max_bpm() -> float:
    return _num_env("MEDIA_MUSIC_BED_MAX_BPM", DEFAULT_MAX_BPM)


def max_onsets() -> float:
    return _num_env("MEDIA_MUSIC_BED_MAX_ONSETS", DEFAULT_MAX_ONSETS)


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


def onset_rate(env: list[float], rate: float = RATE) -> Optional[float]:
    """Onsets a second in a level envelope (`rate` frames a second): rises of
    at least RISE in the log level over two frames, each a local peak, at
    least GAP_S apart. The floor under the log is 2 % of the loud end (the
    95th percentile), so a near-silent tail's noise does not count."""
    n = len(env)
    if n < rate * 10:
        return None
    top = sorted(env)[int(n * 0.95)] or 1.0
    floor = 0.02 * top
    lv = [math.log(e + floor) for e in env]
    flux = [0.0, 0.0] + [max(0.0, lv[i] - lv[i - 2]) for i in range(2, n)]
    gap = int(rate * GAP_S)
    count, last = 0, -gap
    for i in range(1, n - 1):
        f = flux[i]
        if f >= RISE and f >= flux[i - 1] and f >= flux[i + 1] and i - last >= gap:
            count += 1
            last = i
    return round(count / (n / rate), 2)


def _envelope(src: Path) -> list[float]:
    r = subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-ss", "15", "-t", "90",
                        "-i", str(src), "-ac", "1", "-ar", "8000", "-f", "s16le", "-"],
                       stdin=subprocess.DEVNULL, capture_output=True, timeout=120)
    raw = r.stdout
    if len(raw) < 8000 * 2 * 10:
        # Shorter than 25 s: from the start.
        r = subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-t", "90",
                            "-i", str(src), "-ac", "1", "-ar", "8000", "-f", "s16le", "-"],
                           stdin=subprocess.DEVNULL, capture_output=True, timeout=120)
        raw = r.stdout
    pcm = array.array("h")
    pcm.frombytes(raw[: len(raw) // 2 * 2])
    if sys.byteorder != "little":
        pcm.byteswap()
    hop = 8000 // RATE
    return [sum(abs(x) for x in pcm[i:i + hop]) / hop for i in range(0, len(pcm) - hop, hop)]


def measure(src: Path, loudness: bool = True) -> dict:
    """A track's `{"bpm", "onsets", "lufs", "tempo", "energy"}` (tempo and
    energy 0..1 like astrotunes' axes: 60 → 180 BPM, -20 → -8 LUFS), what
    ffmpeg could tell. `loudness` False for a file already normalised (its
    loudness says nothing). Slow: the background process's job."""
    out: dict = {}
    try:
        if loudness:
            r = subprocess.run(["ffmpeg", "-nostdin", "-hide_banner", "-nostats", "-t", "90",
                                "-i", str(src), "-af", "ebur128", "-f", "null", "-"],
                               stdin=subprocess.DEVNULL, capture_output=True, timeout=120)
            m = re.findall(r"I:\s+(-?[0-9.]+) LUFS", r.stderr.decode(errors="replace"))
            if m:
                out["lufs"] = float(m[-1])
        env = _envelope(src)
        bpm = bpm_of(env, RATE)
        if bpm:
            out["bpm"] = bpm
        on = onset_rate(env, RATE)
        if on is not None:
            out["onsets"] = on
    except (OSError, subprocess.SubprocessError, ValueError) as e:
        log.info("music-bed tempo: measuring %s: %s", src, e)
    if "bpm" in out:
        out["tempo"] = round(max(0.0, min(1.0, (out["bpm"] - 60) / 120)), 2)
    if "lufs" in out:
        out["energy"] = round(max(0.0, min(1.0, (out["lufs"] + 20) / 12)), 2)
    return out


def is_slow(features: Optional[dict]) -> bool:
    """Whether a measured track counts as slow (the rule in the module's
    docstring): the BPM and the busyness both say so."""
    f = features or {}
    bpm, onsets = f.get("bpm"), f.get("onsets")
    if not isinstance(bpm, (int, float)) or not isinstance(onsets, (int, float)):
        return False
    lim_on = max_onsets()
    energy = f.get("energy")
    calm = onsets <= lim_on and not (isinstance(energy, (int, float)) and energy > MAX_ENERGY)
    if not calm:
        return False
    lim = max_bpm()
    if bpm <= lim:
        return True
    return bpm / 2 <= lim and onsets <= DOUBLED_ONSETS * lim_on


def keep(entries: Iterable[dict]) -> list[dict]:
    """`entries` as the bed may play them: only the slow ones while slow is
    on (music_bed.slow_on), else all."""
    entries = list(entries)
    from . import music_bed
    if not music_bed.slow_on():
        return entries
    return [e for e in entries if is_slow(e.get("features"))]


def needs_measure(entry: dict) -> bool:
    return "onsets" not in (entry.get("features") or {})


def backfill(entries: list[dict], path_of: Callable[[dict], Path], loudness: bool = False,
             limit: int = 12) -> int:
    """Measure (in place) up to `limit` entries never measured for onsets,
    keeping what was measured before (a favourite's raw loudness). Returns
    how many were measured; the caller saves."""
    done = 0
    for e in entries:
        if done >= limit:
            break
        if not needs_measure(e):
            continue
        try:
            p = path_of(e)
            if not p.is_file():
                continue
        except (OSError, KeyError, TypeError):
            continue
        got = measure(p, loudness=loudness)
        # Marked measured even when ffmpeg said nothing, so it is not tried
        # again on every pass (and stays not slow).
        got.setdefault("onsets", None)
        e["features"] = {**(e.get("features") or {}), **got}
        done += 1
    return done
