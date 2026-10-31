"""Guided meditations: a script spoken slowly over a bed, with real silences.

David, 31 Oct 2026: "I'd like to incorporate relaxation and meditation
features" (docs/proposals/2026-10-31-relax-and-meditate.md, §1). The agent
writes the script; this turns it into one audio file and plays it on the
book channel, as a document is (docs.py): a ten-minute session is a short
audiobook, so Pause, Stop, seek and the mini player already understand it,
and a reply that arrives during it pauses it rather than talking over it.
It is eased out, not cut (David, 31 Oct 2026: "ease out of it instead of
pausing for speech"): faded down over MEDIA_MEDITATE_EASE_OUT_S (5.0) before
the pause and back up over MEDIA_MEDITATE_EASE_IN_S (2.0) after
(route/coordinator.py `_ease_book_out`, SinkBook.fade_volume).

A script is plain text with two markers:

- ``[[pause: 20]]`` — silence with the music still playing: seconds, or
  ``1m``, ``1m30s``, ``90s``; at most MAX_PAUSE_S each.
- ``[[bell]]`` — a singing bowl, synthesised here (`bell_samples`), rung
  out for about BELL_S before the next words.

Every paragraph (text between blank lines or markers) is one clip, said
MEDIA_MEDITATE_RATE (-12%) slower than a reply, with MEDIA_MEDITATE_GAP_S
(2.0) of quiet after it unless a pause follows.

The music: a generated bed (music_bed.render; MEDIA_MEDITATE_MOOD, night
by default) at its own level, MEDIA_MEDITATE_BED_PEAK_DBFS (-20), not the
reply bed's: here it is the point, not the background. It fades in over
the first LEAD_S, before the first words, and out over
MEDIA_MEDITATE_TAIL_S (30) after the last.

Rendered files are kept in ``<cache>/meditations/`` by a hash of the
script and everything that shapes the sound, so playing the same session
again is instant.
"""

from __future__ import annotations

import array
import hashlib
import math
import os
import re
import subprocess
import sys
import wave
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from ._paths import cache_dir

VERSION = 1
RATE = 48000
MAX_PAUSE_S = 600.0
BELL_S = 7.0
LEAD_S = 5.0
FADE_IN_S = 4.0

_MARKER = re.compile(r"\[\[\s*(pause|bell)\s*(?::\s*([^\]]*))?\]\]", re.I)
_DUR = re.compile(r"^\s*(?:(\d+(?:\.\d+)?)\s*m(?:in)?)?\s*(?:(\d+(?:\.\d+)?)\s*s?(?:ec)?)?\s*$", re.I)


@dataclass(frozen=True)
class Step:
    kind: str            # "say" | "pause" | "bell"
    text: str = ""
    seconds: float = 0.0


def _num_env(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, "") or default)
    except ValueError:
        return default


def rate() -> str:
    """How much slower than a reply the voice is, as edge-tts takes it."""
    raw = (os.environ.get("MEDIA_MEDITATE_RATE") or "-12%").strip()
    return raw if raw.endswith("%") else f"{raw}%"


def gap_s() -> float:
    return max(0.0, _num_env("MEDIA_MEDITATE_GAP_S", 2.0))


def tail_s() -> float:
    return max(5.0, _num_env("MEDIA_MEDITATE_TAIL_S", 30.0))


def bed_peak_dbfs() -> float:
    return min(-6.0, _num_env("MEDIA_MEDITATE_BED_PEAK_DBFS", -20.0))


def mood() -> str:
    from . import music_bed
    m = (os.environ.get("MEDIA_MEDITATE_MOOD") or "night").strip()
    return m if m in music_bed.GENERATED else "night"


def ease_out_s() -> float:
    """How long a meditation fades before a reply pauses it."""
    return min(10.0, max(0.0, _num_env("MEDIA_MEDITATE_EASE_OUT_S", 5.0)))


def ease_in_s() -> float:
    """How long it takes to come back up after the reply."""
    return min(10.0, max(0.0, _num_env("MEDIA_MEDITATE_EASE_IN_S", 2.0)))


def is_meditation(path: Optional[str]) -> bool:
    """Whether the book channel's file is one of these renders (by name, so
    a copy on the phone counts too)."""
    name = Path(str(path or "")).name
    return name.startswith("meditation-") and name.endswith(".mp3")


def pause_seconds(raw: str) -> float:
    """``20``, ``20s``, ``1m``, ``1m30s``, ``1.5m`` → seconds (0 if unreadable),
    capped at MAX_PAUSE_S."""
    m = _DUR.match(raw or "")
    if not m or not (m.group(1) or m.group(2)):
        return 0.0
    secs = float(m.group(1) or 0) * 60 + float(m.group(2) or 0)
    return min(MAX_PAUSE_S, secs)


def _paragraphs(text: str) -> list[str]:
    out = []
    for para in re.split(r"\n\s*\n", text):
        said = " ".join(line.strip() for line in para.splitlines() if line.strip())
        if said:
            out.append(said)
    return out


def parse(script: str) -> list[Step]:
    """The script as steps, in order. Unknown markers are left in the words
    (and so said), which is how a typo is noticed."""
    steps: list[Step] = []
    at = 0
    for m in _MARKER.finditer(script):
        steps += [Step("say", p) for p in _paragraphs(script[at:m.start()])]
        if m.group(1).lower() == "bell":
            steps.append(Step("bell"))
        else:
            secs = pause_seconds(m.group(2) or "")
            if secs > 0:
                steps.append(Step("pause", seconds=secs))
        at = m.end()
    steps += [Step("say", p) for p in _paragraphs(script[at:])]
    return steps


def timeline(steps: list[Step], durations: dict[int, float],
             bell_s: float = BELL_S, gap: Optional[float] = None,
             lead: float = LEAD_S) -> list[tuple[float, Step, int]]:
    """When each step starts: ``(start_s, step, index)``. A said step whose
    clip failed (no duration) is left out. Quiet after each said step is
    ``gap``, unless a pause follows it, which is quiet enough."""
    gap = gap_s() if gap is None else gap
    out = []
    clock = lead
    for i, st in enumerate(steps):
        if st.kind == "say":
            d = durations.get(i, 0.0)
            if d <= 0:
                continue
            out.append((clock, st, i))
            clock += d
            nxt = steps[i + 1] if i + 1 < len(steps) else None
            if nxt is None or nxt.kind != "pause":
                clock += gap
        elif st.kind == "pause":
            out.append((clock, st, i))
            clock += st.seconds
        else:
            out.append((clock, st, i))
            clock += bell_s
    return out


def length_s(steps: list[Step], durations: dict[int, float]) -> float:
    """How long the voice part runs, lead-in included, before the tail."""
    tl = timeline(steps, durations)
    if not tl:
        return LEAD_S
    start, st, i = tl[-1]
    if st.kind == "say":
        return start + durations[i]
    return start + (st.seconds if st.kind == "pause" else BELL_S)


# ---- sound -------------------------------------------------------------------

# A singing bowl: inharmonic partials, each a slowly beating pair, the
# higher ones dying first.
_BOWL = ((1.0, 1.0, 6.0), (2.76, 0.45, 3.2), (5.40, 0.22, 1.6), (8.93, 0.10, 0.8))


def bell_samples(f0: float = 196.0, seconds: float = BELL_S,
                 peak_dbfs: float = -14.0) -> array.array:
    """A singing bowl struck once, as 16-bit mono PCM at RATE."""
    n = int(RATE * seconds)
    out = [0.0] * n
    for ratio, amp, tau in _BOWL:
        f = f0 * ratio
        w1 = 2 * math.pi * f / RATE
        w2 = 2 * math.pi * (f + 0.7) / RATE
        k = 1.0 / (tau * RATE)
        for i in range(n):
            env = math.exp(-i * k) * min(1.0, i / 240.0)
            out[i] += amp * env * 0.5 * (math.sin(w1 * i) + math.sin(w2 * i))
    # The last half second fades to nothing, so it never clicks off.
    fade = int(RATE * 0.5)
    for j in range(fade):
        out[n - fade + j] *= 1.0 - j / fade
    peak = max(abs(v) for v in out) or 1.0
    scale = 10 ** (peak_dbfs / 20.0) * 32767 / peak
    return array.array("h", (int(v * scale) for v in out))


def _pcm_bytes(samples: array.array) -> bytes:
    if sys.byteorder != "little":
        samples = array.array("h", samples)
        samples.byteswap()
    return samples.tobytes()


def _write_wav(path: Path, pcm: bytes) -> None:
    tmp = path.with_name(f".{path.name}.{os.getpid()}")
    with wave.open(str(tmp), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(pcm)
    tmp.replace(path)


def out_dir() -> Path:
    return cache_dir() / "meditations"


def bed_file(name: Optional[str] = None) -> Path:
    """The bed's loop at the meditation level, rendered once per mood."""
    from . import music_bed
    name = name or mood()
    peak_db = bed_peak_dbfs()
    p = out_dir() / f"bed-{name}-v{music_bed.VERSION}-p{peak_db:g}.wav"
    if p.is_file() and p.stat().st_size > 44:
        return p
    p.parent.mkdir(parents=True, exist_ok=True)
    samples = music_bed.render(name)
    peak = max((abs(v) for v in samples), default=0.0) or 1.0
    scale = 10 ** (peak_db / 20.0) * 32767 / peak
    _write_wav(p, _pcm_bytes(array.array("h", (int(round(v * scale)) for v in samples))))
    return p


def _decode(clip: Path) -> bytes:
    """A rendered clip as 16-bit mono PCM at RATE (b"" on failure)."""
    try:
        r = subprocess.run(
            ["ffmpeg", "-v", "error", "-i", str(clip), "-f", "s16le",
             "-ac", "1", "-ar", str(RATE), "-"],
            capture_output=True, timeout=120)
    except (OSError, subprocess.SubprocessError):
        return b""
    return r.stdout if r.returncode == 0 else b""


def _silence(seconds: float) -> bytes:
    return b"\x00\x00" * int(round(seconds * RATE))


def voice_track(steps: list[Step], clips: dict[int, bytes]) -> bytes:
    """The words, pauses and bells laid end to end as PCM, lead-in first."""
    durations = {i: len(b) / 2 / RATE for i, b in clips.items() if b}
    bell = _pcm_bytes(bell_samples())
    parts = []
    clock = 0.0
    for start, st, i in timeline(steps, durations):
        if start > clock:
            parts.append(_silence(start - clock))
            clock = start
        if st.kind == "say":
            parts.append(clips[i])
            clock += durations[i]
        elif st.kind == "bell":
            parts.append(bell)
            clock += len(bell) / 2 / RATE
    return b"".join(parts)


def _key(script: str, engine: str, voice: str, bed: str) -> str:
    raw = "|".join([str(VERSION), engine, voice, rate(), bed, f"{bed_peak_dbfs():g}",
                    f"{gap_s():g}", f"{tail_s():g}", script])
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def render(script: str, engine: Optional[str] = None, voice: Optional[str] = None,
           bed: Optional[str] = None, force: bool = False,
           renderer: Optional[Callable] = None) -> Optional[Path]:
    """Render `script` to one mp3 under out_dir(); None if nothing could be
    said. `bed` is a generated mood, or "none" for the voice alone."""
    engine = engine or os.environ.get("MEDIA_RENDER_ENGINE", "edge")
    voice = voice or os.environ.get("MEDIA_RENDER_VOICE_" + engine.upper()) or ""
    bed = bed or mood()
    steps = parse(script)
    if not any(st.kind == "say" for st in steps):
        return None
    outdir = out_dir()
    outdir.mkdir(parents=True, exist_ok=True)
    final = outdir / f"meditation-{_key(script, engine, voice, bed)}.mp3"
    if final.is_file() and final.stat().st_size > 0 and not force:
        return final

    if renderer is None:
        from .render import render_text as renderer
    work = outdir / f".{final.stem}.parts"
    work.mkdir(parents=True, exist_ok=True)
    said = [(i, st.text) for i, st in enumerate(steps) if st.kind == "say"]

    def synth(job) -> tuple[int, bytes]:
        i, text = job
        part = work / f"{i:04d}.mp3"
        ok, _err = renderer(text, part, engine=engine, voice=voice or None,
                            prosody=(rate(), ""))
        if not ok or not part.exists() or part.stat().st_size == 0:
            return i, b""
        return i, _decode(part)

    workers = max(1, int(os.environ.get("MEDIA_RENDER_JOBS", "6") or 6))
    try:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            clips = dict(pool.map(synth, said))
        if not any(clips.values()):
            return None
        pcm = voice_track(steps, clips)
        voice_wav = work / "voice.wav"
        _write_wav(voice_wav, pcm)
        total = len(pcm) / 2 / RATE + tail_s()
        cmd = ["ffmpeg", "-y", "-v", "error", "-i", str(voice_wav)]
        if bed != "none":
            fade_at = max(0.0, total - tail_s())
            cmd += ["-stream_loop", "-1", "-i", str(bed_file(bed)),
                    "-filter_complex",
                    f"[0:a]apad=whole_dur={total:.3f}[v];"
                    f"[1:a]atrim=0:{total:.3f},afade=t=in:d={FADE_IN_S:g},"
                    f"afade=t=out:st={fade_at:.3f}:d={tail_s():g}[b];"
                    "[v][b]amix=inputs=2:duration=first:normalize=0,"
                    "alimiter=limit=0.95[o]",
                    "-map", "[o]"]
        cmd += ["-ac", "1", "-ar", str(RATE), "-c:a", "libmp3lame", "-q:a", "4",
                "-metadata", "title=Meditation", str(final)]
        try:
            subprocess.run(cmd, check=True, capture_output=True, timeout=600)
        except (OSError, subprocess.SubprocessError):
            final.unlink(missing_ok=True)
            return None
    finally:
        for p in work.glob("*"):
            p.unlink(missing_ok=True)
        try:
            work.rmdir()
        except OSError:
            pass
    return final if final.is_file() and final.stat().st_size > 0 else None
