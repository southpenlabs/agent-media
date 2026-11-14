"""Where a song's sections change, and which is the chorus: for the vocal
handover after a long reply (music_bed_vocal; David, 31 Oct 2026: "Just the
chorus").

A standalone script, never imported by agent_media_core at run time: it
needs numpy, which agent-media does not depend on, so music_bed_vocal runs
it with the Python of the Demucs venv (or any with numpy) as a separate
process, by path, with -I:

    <python> -I music_bed_sections.py <audio file>

and reads one line of JSON: ``{"duration": s, "bounds": [s, ...],
"choruses": [[start, end], ...]}``. The audio is decoded by ffmpeg (mono,
11025 Hz). Each ~0.19 s frame gets a 12-bin chroma (FFT bins folded onto
pitch classes) and a few log band energies; the frames are averaged over
~1 s, compared all against all (cosine), and a Foote checkerboard kernel
along the diagonal gives a novelty curve whose peaks are the boundaries.
Each section's mean chroma is compared with every other's: the section
repeated most (by count, then length) and those like it are the choruses.
Best effort: a song with no clear sections gives no bounds, and the
handover falls back to a fixed length.

It also gives each song a **key** and a **beat grid** (docs/proposals/
2026-11-14-music-around-speech.md step four: "Key and a beat grid measured
and stored for each track"), for choosing neighbours by key and tempo and
for starting and ending on a beat later; nothing chooses by them yet:

- ``key``: the whole song's mean chroma correlated with the Krumhansl–Kessler
  major and minor profiles in all twelve keys; the best is the key, with
  its Camelot number (8B C major, 8A A minor; a neighbour is one step round
  the wheel or the same number's other letter) and ``r``, the correlation
  (under about 0.6 it is a guess: modal, atonal, a long intro elsewhere).
- ``beats``: an onset envelope at ~86 frames a second (positive log-spectral
  flux), its autocorrelation over 60–180 BPM weighted towards 120 (so a
  beat is not taken at double or half), refined in 0.05 BPM steps by a comb
  over the whole song, which also gives the phase: ``bpm``, ``offset`` (the
  first beat, seconds) and ``confidence`` (0–1, from the share of the beats
  that land on an onset; low for a piece with no steady pulse).

Either is null when it cannot be told (silence, too short).
"""

from __future__ import annotations

import json
import subprocess
import sys

RATE = 11025
FRAME = 2048
HOP = 2048
BLOCK = 5            # frames a block (~0.93 s)
KERNEL = 12          # blocks each side of a boundary (~11 s)
MIN_GAP = 8          # blocks between boundaries (~7.4 s)
SAME = 0.92          # cosine at which two sections count as the same


def decode(path: str):
    import numpy as np
    raw = subprocess.run(["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error",
                          "-i", path, "-vn", "-ac", "1", "-ar", str(RATE), "-f", "s16le", "-"],
                         stdin=subprocess.DEVNULL, capture_output=True, timeout=600).stdout
    return np.frombuffer(raw, dtype="<i2").astype("float32") / 32768.0


def features(y):
    import numpy as np
    n = 1 + max(0, (len(y) - FRAME) // HOP)
    win = np.hanning(FRAME).astype("float32")
    freqs = np.fft.rfftfreq(FRAME, 1.0 / RATE)
    ok = (freqs > 55) & (freqs < 4000)
    pc = np.zeros(len(freqs), dtype=int)
    pc[ok] = np.round(12 * np.log2(freqs[ok] / 440.0)).astype(int) % 12
    bands = [(0, 150), (150, 600), (600, 2500), (2500, 5500)]
    out = np.zeros((n, 16), dtype="float32")
    for i in range(n):
        seg = y[i * HOP:i * HOP + FRAME]
        if len(seg) < FRAME:
            seg = np.pad(seg, (0, FRAME - len(seg)))
        mag = np.abs(np.fft.rfft(seg * win)) ** 2
        chroma = np.bincount(pc[ok], weights=mag[ok], minlength=12)
        s = chroma.sum()
        out[i, :12] = chroma / s if s > 0 else 0
        for j, (lo, hi) in enumerate(bands):
            out[i, 12 + j] = np.log1p(mag[(freqs >= lo) & (freqs < hi)].sum())
    e = out[:, 12:]
    out[:, 12:] = (e - e.mean(0)) / (e.std(0) + 1e-6) * 0.15
    return out


def blocks(f):
    import numpy as np
    m = len(f) // BLOCK
    if m == 0:
        return f[:0]
    return f[:m * BLOCK].reshape(m, BLOCK, f.shape[1]).mean(1)


def novelty(b):
    import numpy as np
    x = b / (np.linalg.norm(b, axis=1, keepdims=True) + 1e-9)
    s = x @ x.T
    k = KERNEL
    g = np.exp(-0.5 * (np.linspace(-1.5, 1.5, 2 * k) ** 2))
    kern = np.outer(g, g) * np.sign(np.outer(np.r_[-np.ones(k), np.ones(k)],
                                             np.r_[-np.ones(k), np.ones(k)]))
    m = len(s)
    nov = np.zeros(m)
    for i in range(k, m - k):
        nov[i] = (s[i - k:i + k, i - k:i + k] * kern).sum()
    return np.maximum(nov, 0)


def peaks(nov, min_gap: int = MIN_GAP) -> list[int]:
    import numpy as np
    if not len(nov) or nov.max() <= 0:
        return []
    line = np.median(nov) + 0.5 * nov.std()
    cand = [i for i in range(1, len(nov) - 1)
            if nov[i] >= nov[i - 1] and nov[i] > nov[i + 1] and nov[i] > line]
    cand.sort(key=lambda i: -nov[i])
    chosen: list[int] = []
    for i in cand:
        if all(abs(i - j) >= min_gap for j in chosen):
            chosen.append(i)
    return sorted(chosen)


def choruses(b, cuts: list[int], block_s: float) -> list[list[float]]:
    import numpy as np
    edges = [0] + cuts + [len(b)]
    segs = [(edges[i], edges[i + 1]) for i in range(len(edges) - 1) if edges[i + 1] > edges[i]]
    if len(segs) < 3:
        return []
    means = np.array([b[a:z, :12].mean(0) for a, z in segs])
    means = means / (np.linalg.norm(means, axis=1, keepdims=True) + 1e-9)
    sim = means @ means.T
    like = [[j for j in range(len(segs)) if sim[i, j] >= SAME] for i in range(len(segs))]
    best = max(range(len(segs)), key=lambda i: (len(like[i]), segs[i][1] - segs[i][0]))
    if len(like[best]) < 2:
        return []
    return [[round(segs[j][0] * block_s, 2), round(segs[j][1] * block_s, 2)]
            for j in sorted(like[best])]


# Krumhansl–Kessler key profiles, from the tonic up.
MAJOR = [6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88]
MINOR = [6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17]
#: Pitch classes from C, as a key is named.
NAMES = ["C", "C#", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B"]


def camelot(tonic: int, minor: bool) -> str:
    """The Camelot wheel's name for a key (tonic from C): C major 8B, A minor
    8A; a step round the wheel is a fifth, the letter its relative."""
    c = (tonic + 3) % 12 if minor else tonic
    n = (8 + 7 * c) % 12 or 12
    return f"{n}{'A' if minor else 'B'}"


def key_of(chroma) -> "dict | None":
    """The key of a mean chroma whose index 0 is A (features' bins)."""
    import numpy as np
    c = np.roll(np.asarray(chroma, dtype="float64"), -3)   # index 0 is C
    if not np.isfinite(c).all() or c.std() <= 1e-9:
        return None
    best = None
    for minor, prof in ((False, MAJOR), (True, MINOR)):
        for t in range(12):
            r = float(np.corrcoef(c, np.roll(prof, t))[0, 1])
            if best is None or r > best[0]:
                best = (r, t, minor)
    r, t, minor = best
    return {"tonic": NAMES[t], "mode": "minor" if minor else "major",
            "name": f"{NAMES[t]} {'minor' if minor else 'major'}",
            "camelot": camelot(t, minor), "r": round(r, 3)}


ORATE_HOP = 128        # the onset envelope's hop: ~86 frames a second
#: The least flux that is an onset at all: a held chord's numerical noise peaked at 7, the kept
#: originals (normalised to -16 LUFS) sat at 50-80 at their 95th percentile (14 Nov 2026).
ONSET_FLOOR = 15.0
ORATE_FRAME = 1024


def onset_envelope(y):
    """Positive log-spectral flux a frame, its local mean taken off."""
    import numpy as np
    n = 1 + max(0, (len(y) - ORATE_FRAME) // ORATE_HOP)
    if n < 3:
        return np.zeros(0)
    win = np.hanning(ORATE_FRAME).astype("float32")
    env = np.zeros(n, dtype="float64")
    prev = None
    step = 2048
    for a in range(0, n, step):
        idx = np.arange(a, min(n, a + step))[:, None] * ORATE_HOP + np.arange(ORATE_FRAME)[None, :]
        idx = np.minimum(idx, len(y) - 1)
        mag = np.log1p(100 * np.abs(np.fft.rfft(y[idx] * win, axis=1)))
        lead = mag[:1] if prev is None else prev[None, :]
        d = np.diff(np.vstack([lead, mag]), axis=0)
        env[a:a + len(mag)] = np.maximum(d, 0).sum(1)
        prev = mag[-1]
    w = max(1, int(round(0.5 * RATE / ORATE_HOP)))
    local = np.convolve(env, np.ones(w) / w, mode="same")
    return np.maximum(env - local, 0)


def _comb(env, period: float):
    """For each phase of `period` frames: the mean envelope on its beats; the best phase and the mean over phases."""
    import numpy as np
    k = np.arange(int((len(env) - 1) / period))
    if len(k) < 4:
        return 0.0, 0.0, 0.0
    phases = np.arange(int(period))
    pos = np.rint(phases[:, None] + k[None, :] * period).astype(int)
    pos = np.minimum(pos, len(env) - 1)
    score = env[pos].mean(1)
    i = int(score.argmax())
    return float(score[i]), float(score.mean()), float(phases[i])


def beats_of(env, fps: float) -> "dict | None":
    """The beat grid: tempo, the first beat's time, how clear the pulse is."""
    import numpy as np
    if len(env) < fps * 8 or env.std() <= 1e-9:
        return None
    x = env - env.mean()
    n = len(x)
    ac = np.fft.irfft(np.abs(np.fft.rfft(x, 2 * n)) ** 2)[:n]
    best = None
    for bpm in np.arange(60.0, 180.0, 0.5):
        lag = fps * 60.0 / bpm
        lo = int(lag)
        if lo + 1 >= n:
            continue
        v = ac[lo] + (lag - lo) * (ac[lo + 1] - ac[lo])
        w = np.exp(-0.5 * (np.log2(bpm / 120.0) / 0.9) ** 2)
        if best is None or v * w > best[0]:
            best = (v * w, bpm)
    if best is None:
        return None
    top = None
    for bpm in np.arange(best[1] - 1.5, best[1] + 1.5, 0.05):
        peak, mean, phase = _comb(env, fps * 60.0 / bpm)
        if top is None or peak > top[0]:
            top = (peak, mean, phase, bpm)
    peak, mean, phase, bpm = top
    period = fps * 60.0 / bpm
    # How clear the pulse is: the share of its beats that land on an onset (the envelope within a
    # frame of the beat above twice its mean). One loud start, as a held chord has, is one beat of
    # many; a click track is all of them. (A comb's mean was fooled by that one start.)
    k = np.arange(int((len(env) - 1 - phase) / period))
    pos = np.rint(phase + k * period).astype(int)
    near = np.maximum.reduce([env[np.clip(pos + d, 0, len(env) - 1)] for d in (-1, 0, 1)])
    hit = float((near > max(2.0 * env.mean(), ONSET_FLOOR)).mean()) if len(pos) else 0.0
    conf = max(0.0, min(1.0, (hit - 0.15) / 0.6))
    # A frame's flux peaks when an onset is about three quarters into its window, not at its start.
    first = (phase / fps + 0.75 * ORATE_FRAME / RATE) % (60.0 / bpm)
    return {"bpm": round(float(bpm), 1), "offset": round(float(first), 3), "confidence": round(conf, 2)}


def analyse(path: str) -> dict:
    y = decode(path)
    dur = len(y) / RATE
    f = features(y)
    b = blocks(f)
    block_s = BLOCK * HOP / RATE
    cuts = peaks(novelty(b)) if len(b) > 2 * KERNEL else []
    return {"duration": round(dur, 2),
            "bounds": [round(c * block_s, 2) for c in cuts],
            "choruses": choruses(b, cuts, block_s),
            "key": key_of(f[:, :12].mean(0)) if len(f) else None,
            "beats": beats_of(onset_envelope(y), RATE / ORATE_HOP)}


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("usage: music_bed_sections.py <audio file>", file=sys.stderr)
        raise SystemExit(2)
    print(json.dumps(analyse(sys.argv[1])))
