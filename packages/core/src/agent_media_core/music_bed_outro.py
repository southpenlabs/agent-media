"""An ending for each generated bed (roadmap 39, music_bed_mix "outro";
David, 31 Oct 2026: the music "finishes rather than just fading").

The loop's last chord and then its first, the tonic, rung out on the
style's own instrument: about 3.5 s, played once as the last thing after the
voice while the loop fades under it. Rendered beside the beds by the same
detached process (music_bed.warm), at the same peak (music_bed.peak_dbfs)
and named for the same level: `music-bed-<style>-end-v<N>[-g<dB>].wav`.
Nature has no chords, and so no ending: it fades with the sweep.
"""

from __future__ import annotations

import array
import os
import random
import sys
import wave
from pathlib import Path
from typing import Optional

#: The approach and the closing chord, by index into the style's chords,
#: where last-then-first does not cadence (jazz's loop starts on its ii).
_CADENCE = {"jazz": (1, 2)}
SECONDS = 3.6
#: The approach chord's length before the close.
APPROACH_S = 0.9


def styles() -> tuple[str, ...]:
    """The beds that have an ending: every generated style but nature, and
    the kind beds (a slow version uses its style's ending)."""
    from . import music_bed as B
    return tuple(s for s in B.GENERATED + B.KIND_BEDS if s != "nature")


def _base(style: str) -> str:
    from . import music_bed as B
    return B._split_slow(style)[0]


def has_ending(style: str) -> bool:
    return _base(style) in styles()


def _chords(base: str) -> list[tuple[int, list[int]]]:
    from . import music_bed as B
    if base in B.GENRES:
        from . import music_bed_styles as S
        out = []
        for c in S.RECIPES[base]["chords"]:
            if isinstance(c, tuple):
                out.append((c[0], list(c[1])))
            else:                    # ambient: notes only, the lowest the root
                out.append((min(c) - 12, list(c)))
        return out
    return [(r, list(ch)) for r, ch in B._RECIPES[base][0]]


def _add(dst: list[float], src: list[float], at_s: float, g: float) -> None:
    from . import music_bed as B
    at = int(at_s * B.RATE)
    for i, v in enumerate(src):
        j = at + i
        if j >= len(dst):
            break
        if j >= 0:
            dst[j] += g * v


def _voice(base: str, root: int, chord: list[int], d: float, rng) -> list[list[float]]:
    """(parts, gains) of one chord on `base`'s instrument, held `d` s."""
    from . import music_bed as B
    from . import music_bed_styles as S
    hz = B._hz
    if base == "piano":
        notes = [S._felt(hz(root + 12), d, 0.5, 0.8)] + [
            S._felt(hz(m + 12), d, 0.6, 1.0) for m in chord]
        return [(n, 0.5, 0.03 * k) for k, n in enumerate(notes)]
    if base in ("lofi", "jazz"):
        notes = [(S._pluck_bass(hz(root), d), 0.6, 0.0)]
        notes += [(S._rhodes(hz(m), d, 0.8), 0.35, 0.02 * k) for k, m in enumerate(chord)]
        return notes
    if base == "bossa":
        notes = [(S._nylon(hz(root + 12), d, 0.8), 0.5, 0.0)]
        notes += [(S._nylon(hz(m), d, 0.7, seed=m), 0.4, 0.035 * (k + 1))
                  for k, m in enumerate(chord)]
        return notes
    if base == "ambient":
        freqs = [hz(m) for m in chord]
        return [(S._swell_pad(freqs, d * 1.3, rng), 0.9, -0.3),
                (S._bell(hz(chord[-1] + 12), d), 0.12, 0.2)]
    # The moods and kind beds: a pad, the root on electric piano, the chord's
    # notes rolled an octave up.
    notes = [(B._pad([hz(m) for m in chord], d + 0.4, 0.3), 0.5, 0.0),
             (B._epiano(hz(root - 12), d, 0.9, 0.25), 0.35, 0.0)]
    notes += [(B._epiano(hz(m + 12), d, 1.0, 0.25), 0.22, 0.06 * k)
              for k, m in enumerate(chord)]
    return notes


def render(style: str) -> list[float]:
    """The ending of `style` (or its slow version), -1..1 before scaling."""
    from . import music_bed as B
    base = _base(style)
    chords = _chords(base)
    a, t = _CADENCE.get(base, (len(chords) - 1, 0))
    rng = random.Random(41)
    n = int(SECONDS * B.RATE)
    s = [0.0] * n
    for src, g, at in _voice(base, *chords[a], APPROACH_S + 0.3, rng):
        _add(s, src, max(0.0, at), g * 0.8)
    for src, g, at in _voice(base, *chords[t], SECONDS - APPROACH_S, rng):
        _add(s, src, APPROACH_S + max(0.0, at), g)
    # The whole of it dies away by the end: nothing cut off mid-note.
    fade = int(1.2 * B.RATE)
    for k in range(fade):
        s[n - fade + k] *= (1 - k / fade) ** 2
    return s


def file_for(style: str) -> Path:
    from . import music_bed as B
    base = _base(style)
    g = B.gain_db()
    tag = "" if g == B.DEFAULT_GAIN_DB else f"-g{g:g}"
    return B._dir() / f"music-bed-{base}-end-v{B.VERSION}{tag}.wav"


def rendered(style: str) -> Optional[Path]:
    try:
        p = file_for(style)
        return p if p.is_file() and p.stat().st_size > 44 else None
    except OSError:
        return None


def ready(style: str) -> Optional[Path]:
    """The ending's file at the chosen level, else one at another level (a
    phone and this host play it at the bed's live offset, as the bed); None
    when there is none or `style` has no ending. Never renders."""
    if not has_ending(style):
        return None
    p = rendered(style)
    if p is not None:
        return p
    from . import music_bed as B
    try:
        base = _base(style)
        for q in sorted(B._dir().glob(f"music-bed-{base}-end-v{B.VERSION}*.wav")):
            if q.stat().st_size > 44:
                return q
    except OSError:
        pass
    return None


def write(style: str) -> Path:
    """Render `style`'s ending and write its file (atomically). Slow-ish."""
    from . import music_bed as B
    p = file_for(style)
    p.parent.mkdir(parents=True, exist_ok=True)
    samples = render(style)
    peak = max((abs(v) for v in samples), default=0.0) or 1.0
    scale = (10 ** (B.peak_dbfs(_base(style)) / 20.0)) * 32767 / peak
    pcm = array.array("h", (int(round(v * scale)) for v in samples))
    if sys.byteorder != "little":
        pcm.byteswap()
    tmp = p.with_name(f".{p.name}.{os.getpid()}")
    with wave.open(str(tmp), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(B.RATE)
        w.writeframes(pcm.tobytes())
    os.replace(tmp, p)
    return p


def names_kept() -> set[str]:
    """The ending files music_bed.prune_old keeps: the current level's."""
    return {file_for(s).name for s in styles()}
