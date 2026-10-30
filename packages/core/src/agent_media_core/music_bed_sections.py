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


def analyse(path: str) -> dict:
    y = decode(path)
    dur = len(y) / RATE
    b = blocks(features(y))
    block_s = BLOCK * HOP / RATE
    cuts = peaks(novelty(b)) if len(b) > 2 * KERNEL else []
    return {"duration": round(dur, 2),
            "bounds": [round(c * block_s, 2) for c in cuts],
            "choruses": choruses(b, cuts, block_s)}


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("usage: music_bed_sections.py <audio file>", file=sys.stderr)
        raise SystemExit(2)
    print(json.dumps(analyse(sys.argv[1])))
