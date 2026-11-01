"""A breathing pacer: the music swells as you breathe in and ebbs as you
breathe out, with a soft spoken count for the first rounds
(docs/proposals/2026-10-31-relax-and-meditate.md §3; David, 1 Nov 2026:
"yeah, let's do it").

Rendered to one file and played on the book channel like a meditation
(meditate.py): its name starts ``meditation-``, so a reply eases it out and
back in. Patterns (seconds in, hold, out, hold):

- **box** 4-4-4-4, **478** 4-7-8-0, **slow** 5-0-5-0 (about six breaths a
  minute), **calm** 4-0-6-0 (a longer out-breath).

The bed (meditate.bed_file, MEDIA_MEDITATE_MOOD) runs under it all; its
level follows the breath between MEDIA_BREATHE_LOW_DB (-14) under the
meditation level and that level, rising over the in-breath, held, falling
over the out-breath (an ffmpeg volume expression on time, so nothing is
computed sample by sample here). "Breathe in", "hold" and "breathe out" are
said at each phase's start for MEDIA_BREATHE_CUE_ROUNDS (3) rounds, then the
music alone carries it. A bell starts and ends it.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
from pathlib import Path
from typing import Callable, Optional

from . import meditate as M

PATTERNS = {"box": (4, 4, 4, 4), "478": (4, 7, 8, 0), "slow": (5, 0, 5, 0), "calm": (4, 0, 6, 0)}
CUES = ("Breathe in", "Hold", "Breathe out", "Hold")
LEAD_S = 6.0
VERSION = 1


def _num(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name) or default)
    except ValueError:
        return default


def pattern(name: str) -> tuple[int, int, int, int]:
    p = PATTERNS.get((name or "box").strip().lower())
    if p is None:
        raise ValueError(f"pattern must be one of {', '.join(PATTERNS)}")
    return p


def rounds(pat: tuple, minutes: float) -> int:
    return max(1, int(round(minutes * 60 / sum(pat))))


def cue_times(pat: tuple, n_rounds: int, cue_rounds: int) -> list[tuple[float, int]]:
    """(start in seconds after the lead-in, which cue) for the first rounds."""
    out, t = [], 0.0
    for r in range(min(n_rounds, cue_rounds)):
        for i, d in enumerate(pat):
            if d > 0:
                out.append((t, i))
            t += d
    return out


def gain_expr(pat: tuple, low_db: float, start: float) -> str:
    """An ffmpeg volume expression (eval=frame): the breath's level as an
    amplitude, periodic from `start`."""
    i, h1, o, h2 = pat
    period = sum(pat)
    lo = 10 ** (low_db / 20.0)
    x = f"mod(t-{start:g},{period})"
    up = f"({lo:.4f}+(1-{lo:.4f})*({x})/{i})" if i else "1"
    down = f"(1-(1-{lo:.4f})*(({x})-{i + h1})/{o})" if o else f"{lo:.4f}"
    return (f"if(lt(t,{start:g}),{lo:.4f},"
            f"if(lt({x},{i}),{up},"
            f"if(lt({x},{i + h1}),1,"
            f"if(lt({x},{i + h1 + o}),{down},{lo:.4f}))))")


def render(name: str = "box", minutes: float = 5.0, bed: Optional[str] = None,
           renderer: Optional[Callable] = None, force: bool = False) -> Optional[Path]:
    pat = pattern(name)
    minutes = max(1.0, min(60.0, float(minutes)))
    n = rounds(pat, minutes)
    cue_rounds = int(_num("MEDIA_BREATHE_CUE_ROUNDS", 3))
    low_db = min(-3.0, _num("MEDIA_BREATHE_LOW_DB", -14))
    bed = bed or M.mood()
    engine = os.environ.get("MEDIA_RENDER_ENGINE", "edge")
    voice = os.environ.get("MEDIA_RENDER_VOICE_" + engine.upper()) or ""
    key = hashlib.sha256("|".join(map(str, (VERSION, name, n, cue_rounds, low_db, bed, engine,
                                             voice, M.rate(), M.bed_peak_dbfs()))).encode()).hexdigest()[:16]
    outdir = M.out_dir()
    outdir.mkdir(parents=True, exist_ok=True)
    final = outdir / f"meditation-breathe-{name}-{key}.mp3"
    if final.is_file() and final.stat().st_size > 0 and not force:
        return final
    if renderer is None:
        from .render import render_text as renderer
    work = outdir / f".{final.stem}.parts"
    work.mkdir(parents=True, exist_ok=True)
    try:
        cues = {}
        for i, text in enumerate(CUES[:3]):
            part = work / f"cue{i}.mp3"
            ok, _ = renderer(text, part, engine=engine, voice=voice or None, prosody=(M.rate(), ""))
            cues[i] = M._decode(part) if ok and part.exists() else b""
        cues[3] = cues[1]
        bell = M._pcm_bytes(M.bell_samples())
        body_s = n * sum(pat)
        total = LEAD_S + body_s + M.BELL_S + 4.0
        pcm = bytearray(M._silence(total))

        def lay(at: float, clip: bytes) -> None:
            o = int(at * M.RATE) * 2
            pcm[o:o + len(clip)] = clip[:max(0, len(pcm) - o)]

        lay(0.0, bell)
        for at, which in cue_times(pat, n, cue_rounds):
            if cues.get(which):
                lay(LEAD_S + at, cues[which])
        lay(LEAD_S + body_s, bell)
        voice_wav = work / "voice.wav"
        M._write_wav(voice_wav, bytes(pcm))
        cmd = ["ffmpeg", "-y", "-v", "error", "-i", str(voice_wav)]
        if bed != "none":
            fade_at = max(0.0, total - 6.0)
            cmd += ["-stream_loop", "-1", "-i", str(M.bed_file(bed)), "-filter_complex",
                    f"[1:a]atrim=0:{total:.3f},volume='{gain_expr(pat, low_db, LEAD_S)}':eval=frame,"
                    f"afade=t=in:d=3,afade=t=out:st={fade_at:.3f}:d=6[b];"
                    "[0:a][b]amix=inputs=2:duration=first:normalize=0,alimiter=limit=0.95[o]",
                    "-map", "[o]"]
        cmd += ["-ac", "1", "-ar", str(M.RATE), "-c:a", "libmp3lame", "-q:a", "4",
                "-metadata", f"title=Breathing ({name})", str(final)]
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
