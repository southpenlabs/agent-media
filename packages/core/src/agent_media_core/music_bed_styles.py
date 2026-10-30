"""The music bed's genre styles: generated music beside the six moods.

Roadmap item 39 (David, 26 Oct 2026: "let's go with the three options for
music that you gave me earlier"). Five styles, each a fixed choice in
Settings (Auto still picks among the moods):

- **ambient** — long evolving pads, four chords of ten seconds, with soft
  bells drifting over them.
- **piano** — solo felt piano: a muffled, slow-attack tone, the left hand on
  the bar, the right hand wandering the chord.
- **lofi** — Rhodes chords at 66 bpm over a soft, low-passed brushed beat
  (kick, brush snare on 2 and 4, swung ticks) and vinyl hiss with crackle.
- **jazz** — rootless ii-V-I voicings in F, comped in a swung Charleston
  rhythm over a walking bass and brushes.
- **bossa** — bossa nova (David, 30 Oct 2026: "Bossa Nova versions are good
  too"): plucked nylon-string chords (maj7, m9, 6/9) in the bossa's
  syncopated comping at 72 bpm, a soft rim click on the clave, a gentle bass
  on roots and fifths. Slow by nature.
- **nature** — rain, a stream and birds, no music.

**Slow versions** (SLOW_RECIPES; David, 30 Oct 2026: "I think the music
should be restricted to slower variations"): while slow is on
(music_bed.slow_on) lo-fi and jazz play as `lofi-slow` and `jazz-slow`:

- lo-fi at 54 bpm, not 66: a soft kick on one and a brush on three only, no
  ticks and no pushed chord;
- jazz at 60 bpm, not a swung 100: one held chord a bar, the bass on one and
  three (root and fifth), the brushes' swish alone.

Tempo of each, as rendered: ambient a chord every 10 s, no beat; piano 60
bpm, up to five soft notes a bar, no beat; lo-fi 66 bpm with a brushed beat
and swung ticks (quick in feel); jazz 100 bpm swung with a walking bass
(quick); bossa 72 bpm, rim clicks on the clave only; nature no music.

Stdlib only, like the moods, and rendered by the same background process
(music_bed.warm). Every style is one seamless loop: notes that ring past the
end are wrapped to the start (music_bed._wrap_mix), and every continuous
noise (hiss, rain, water, brush swish) is made a second longer than the loop
and crossfaded over its own head (`_seamless`), so the last sample leads
into the first. music_bed.synth scales each to -24 dBFS peak.

Under a voice on a phone: a phone's speaker loses most of what is under
about 300 Hz, so every style keeps its body above it (voicings from A3 up,
bells, brushes, hiss), and the bass is a quiet extra, not the floor.

Each recipe is a dict in RECIPES, so the tests can render a short version.
"""

from __future__ import annotations

import math
import random
from typing import Callable, Optional

from . import music_bed as B

RATE = B.RATE
TAU = 2 * math.pi

STYLES = ("ambient", "piano", "lofi", "jazz", "bossa", "nature")
LABELS = {"ambient": "Ambient", "piano": "Piano", "lofi": "Lo-fi",
          "jazz": "Jazz chords", "bossa": "Bossa nova",
          "nature": "Nature (rain, stream, birds)"}


# ---- building blocks ------------------------------------------------------------

def _seamless(xs: list[float], n: int) -> list[float]:
    """`xs` (at least n + RATE samples) as an n-sample loop: its tail past n
    crossfaded over its head, so the loop's last sample runs into its first."""
    x = min(RATE, len(xs) - n, n)
    out = xs[:n]
    for i in range(x):
        a = 0.5 - 0.5 * math.cos(math.pi * i / x)
        out[i] = a * xs[i] + (1 - a) * xs[n + i]
    return out


def _lp(xs: list[float], fc: float) -> list[float]:
    """One-pole low-pass at `fc` Hz."""
    a = 1 - math.exp(-TAU * fc / RATE)
    y = 0.0
    out = []
    for v in xs:
        y += a * (v - y)
        out.append(y)
    return out


def _hp(xs: list[float], fc: float) -> list[float]:
    """One-pole high-pass at `fc` Hz."""
    lo = _lp(xs, fc)
    return [v - w for v, w in zip(xs, lo)]


def _noise(n: int, rng: random.Random) -> list[float]:
    return [rng.uniform(-1, 1) for _ in range(n)]


def _release(out: list[float], ms: float = 30.0) -> list[float]:
    """The last `ms` faded out (a raised cosine), so no note ends in a click."""
    n = len(out)
    r = min(n, int(RATE * ms / 1000))
    for k in range(r):
        out[n - r + k] *= 0.5 + 0.5 * math.cos(math.pi * (k + 1) / r)
    return out


def _felt(f: float, d: float, vel: float = 1.0, bright: float = 1.0) -> list[float]:
    """A felt piano note: a soft attack, dark partials that die faster the
    higher they are, a touch of string stretch, and a hint of hammer."""
    n = int(RATE * d)
    parts = []
    for k in range(1, 7):
        fk = f * k * (1 + 0.0004 * k * k)
        if fk > 9000:
            break
        amp = (1.0 / k ** 2.0) * (bright if k > 1 else 1.0)
        parts.append((TAU * fk / RATE, amp, (0.7 + 0.8 * k) / RATE))
    att = RATE * 0.012
    out = [0.0] * n
    for w, amp, dec in parts:
        for i in range(n):
            out[i] += amp * math.exp(-dec * i) * math.sin(w * i)
    for i in range(min(n, int(att))):
        out[i] *= i / att
    # The hammer on the felt: 15 ms of soft, low-passed noise.
    rng = random.Random(int(f * 10))
    thump = _lp(_noise(int(RATE * 0.015), rng), 1200)
    for i, v in enumerate(thump[:n]):
        out[i] += 0.08 * v * (1 - i / len(thump))
    return [vel * v for v in _release(out, 40)]


def _rhodes(f: float, d: float, vel: float = 1.0) -> list[float]:
    """An electric piano tine: the fundamental, a bark that fades quickly,
    a bell partial, and a slow tremolo."""
    n = int(RATE * d)
    w = TAU * f / RATE
    trem = TAU * 4.2 / RATE
    out = []
    for i in range(n):
        e = math.exp(-1.1 * i / RATE)
        v = (math.sin(w * i)
             + 0.35 * math.sin(2 * w * i) * math.exp(-5 * i / RATE)
             + 0.08 * math.sin(7.1 * w * i) * math.exp(-9 * i / RATE))
        out.append(e * v * (1 + 0.12 * math.sin(trem * i)))
    for i in range(min(n, int(RATE * 0.006))):
        out[i] *= i / (RATE * 0.006)
    return [vel * v for v in _release(out, 40)]


def _pluck_bass(f: float, d: float) -> list[float]:
    """A soft upright-ish bass: a round tone with its octave, dying away."""
    n = int(RATE * d)
    w = TAU * f / RATE
    out = [math.exp(-2.2 * i / RATE)
           * (math.sin(w * i) + 0.45 * math.sin(2 * w * i) + 0.2 * math.sin(3 * w * i))
           for i in range(n)]
    for i in range(min(n, int(RATE * 0.01))):
        out[i] *= i / (RATE * 0.01)
    return _release(out, 40)


def _bell(f: float, d: float) -> list[float]:
    """A soft glassy bell: a slow attack, inharmonic partials, a long tail."""
    n = int(RATE * d)
    ws = [(TAU * f / RATE, 1.0), (TAU * f * 2.76 / RATE, 0.25), (TAU * f * 5.4 / RATE, 0.08)]
    att = RATE * 0.4
    out = []
    for i in range(n):
        env = min(1.0, i / att) * math.exp(-0.7 * i / RATE)
        out.append(env * sum(a * math.sin(w * i) for w, a in ws))
    return _release(out, 80)


def _swell_pad(freqs: list[float], d: float, rng: random.Random) -> list[float]:
    """An evolving pad: each note two slightly detuned voices whose levels
    drift on their own slow curves, the whole swelling in and out."""
    n = int(RATE * d)
    out = [0.0] * n
    for f in freqs:
        for det in (0.9985, 1.0015):
            w = TAU * f * det / RATE
            ph = rng.uniform(0, TAU)
            lfo = TAU * rng.uniform(0.05, 0.13) / RATE
            lph = rng.uniform(0, TAU)
            for i in range(n):
                out[i] += (0.75 + 0.25 * math.sin(lfo * i + lph)) * math.sin(w * i + ph)
    k = 2 * len(freqs)
    return [math.sin(math.pi * i / n) ** 2 * v / k for i, v in enumerate(out)]


def _kick(vel: float = 1.0) -> list[float]:
    """A soft kick: a sine falling from 110 to 50 Hz, with a muffled click
    so it is heard on a small speaker."""
    n = int(RATE * 0.3)
    out = []
    ph = 0.0
    for i in range(n):
        t = i / RATE
        f = 50 + 60 * math.exp(-t * 25)
        ph += TAU * f / RATE
        out.append(math.sin(ph) * math.exp(-t * 11))
    click = _lp(_noise(int(RATE * 0.008), random.Random(3)), 2500)
    for i, v in enumerate(click):
        out[i] += 0.25 * v
    return [vel * v for v in _release(out, 20)]


def _brush(d: float, vel: float, rng: random.Random, fc_lo: float = 400,
           fc_hi: float = 3000, attack: float = 0.012) -> list[float]:
    """A brush on the snare: band-limited noise, swept in and dying away."""
    n = int(RATE * d)
    x = _lp(_hp(_noise(n, rng), fc_lo), fc_hi)
    a = RATE * attack
    return _release([vel * v * min(1.0, i / a) * math.exp(-i / (RATE * d * 0.35))
                     for i, v in enumerate(x)], 20)


def _tick(vel: float, rng: random.Random) -> list[float]:
    """A soft hat or ride tick, low-passed so it never hisses."""
    n = int(RATE * 0.06)
    x = _lp(_hp(_noise(n, rng), 3000), 6000)
    return [vel * v * math.exp(-i / (RATE * 0.015)) for i, v in enumerate(x)]


def _chirp(rng: random.Random) -> list[float]:
    """One bird's phrase: two to five quick rising or falling whistles."""
    out: list[float] = []
    for _ in range(rng.randint(2, 5)):
        d = rng.uniform(0.04, 0.12)
        n = int(RATE * d)
        f0 = rng.uniform(2400, 4200)
        f1 = f0 * rng.uniform(0.75, 1.35)
        vib = rng.uniform(25, 45)
        ph = 0.0
        for i in range(n):
            t = i / n
            f = f0 + (f1 - f0) * t + 120 * math.sin(TAU * vib * i / RATE)
            ph += TAU * f / RATE
            out.append(math.sin(math.pi * t) ** 2 * math.sin(ph))
        out.extend([0.0] * int(RATE * rng.uniform(0.03, 0.09)))
    return out


def _nylon(f: float, d: float, vel: float = 1.0, seed: int = 0) -> list[float]:
    """A plucked nylon string (Karplus-Strong): a burst of soft noise
    through a delay line one period long, averaged as it goes round, so the
    highs die first and it rings warm."""
    n = int(RATE * d)
    period = max(2, int(round(RATE / f)))
    rng = random.Random(seed or int(f * 7))
    # A thumb, not a pick: the burst low-passed.
    buf = _lp(_noise(period, rng), min(4000.0, f * 6))
    peak = max(abs(v) for v in buf) or 1.0
    buf = [v / peak for v in buf]
    out = [0.0] * n
    damp = 0.996
    for i in range(n):
        j = i % period
        v = buf[j]
        out[i] = v
        buf[j] = damp * 0.5 * (v + buf[(j + 1) % period])
    for i in range(min(n, int(RATE * 0.003))):
        out[i] *= i / (RATE * 0.003)
    return [vel * v for v in _release(out, 60)]


def _rim(vel: float, rng: random.Random) -> list[float]:
    """A soft rim click: a short knock with a woody ring near 1.7 kHz."""
    n = int(RATE * 0.07)
    w = TAU * 1700 / RATE
    knock = _lp(_hp(_noise(n, rng), 1500), 5000)
    return [vel * (0.6 * math.sin(w * i) + 0.4 * knock[i]) * math.exp(-i / (RATE * 0.012))
            for i in range(n)]


def _mix(dst: list[float], src: list[float], at_s: float, g: float) -> None:
    B._wrap_mix(dst, src, at_s, g)


# ---- the recipes ---------------------------------------------------------------------

#: Every style's recipe. `bars`/`bar` set the loop's length (bars x bar s);
#: chords are MIDI note lists. The tests swap in short ones.
RECIPES: dict[str, dict] = {
    # Dmaj9 Bm9 Gmaj9(#11) A6/9, ten seconds each: 40 s.
    "ambient": {"bar": 10.0, "seed": 21,
                "chords": [[62, 66, 69, 73, 76], [59, 62, 66, 69, 73],
                           [55, 62, 66, 69, 73], [57, 61, 64, 66, 71]],
                "bells": 2},
    # Fmaj7 Em7 Dm7 Cmaj7 twice, four seconds a bar: 32 s.
    "piano": {"bar": 4.0, "rounds": 2, "seed": 9,
              "chords": [(41, [57, 60, 64, 65]), (40, [55, 59, 62, 64]),
                         (38, [53, 57, 60, 62]), (36, [55, 59, 60, 64])],
              "pattern": [0.0, 1.0, 1.5, 2.5, 3.0]},
    # Fmaj9 Em7 Dm9 Cmaj9 twice at 66 bpm: 29 s.
    "lofi": {"bpm": 66.0, "rounds": 2, "seed": 4,
             "chords": [(41, [57, 60, 64, 67]), (40, [55, 59, 62, 67]),
                        (38, [57, 60, 64, 65]), (36, [55, 59, 62, 64])],
             "hiss": 0.05},
    # | Gm9 | C13 | Fmaj9 | D7b9 | Gm9 | C13 | Fmaj9 | Fmaj9 | twice at
    # 100 bpm, swung: 38.4 s. Rootless left-hand voicings.
    "jazz": {"bpm": 100.0, "rounds": 2, "seed": 13,
             "chords": [(43, [58, 62, 65, 69]), (48, [58, 62, 64, 69]),
                        (41, [57, 60, 64, 67]), (50, [60, 63, 66, 69]),
                        (43, [58, 62, 65, 69]), (48, [58, 62, 64, 69]),
                        (41, [57, 60, 64, 67]), (41, [57, 60, 64, 67])]},
    # | Cmaj7 | Cmaj7 | Am9 | Am9 | Dm9 | Dm9 | F6/9 | G6/9 | at 72 bpm:
    # 26.7 s. Guitar voicings from A3 up; the bass an octave over its root.
    "bossa": {"bpm": 72.0, "rounds": 1, "seed": 27,
              "chords": [(48, [60, 64, 67, 71]), (48, [59, 64, 67, 72]),
                         (45, [57, 60, 64, 67, 71]), (45, [59, 64, 67, 72]),
                         (50, [57, 60, 64, 65, 69]), (50, [60, 64, 65, 69]),
                         (41, [57, 62, 65, 67, 69]), (43, [59, 62, 64, 67, 69])]},
    # 40 s of rain, water and birds.
    "nature": {"seconds": 40.0, "seed": 31, "birds": 7},
}

#: The slow versions (music_bed.SLOWED: `<style>-slow`), played while slow
#: is on: the same chords, slower and sparser, the beat much softer or gone.
SLOW_RECIPES: dict[str, dict] = {
    # 54 bpm, twice round: 35.6 s.
    "lofi": {**RECIPES["lofi"], "bpm": 54.0, "slow": True},
    # 60 bpm, once round: 32 s.
    "jazz": {**RECIPES["jazz"], "bpm": 60.0, "rounds": 1, "slow": True},
}


def _recipe(style: str, slow: bool = False) -> dict:
    return SLOW_RECIPES[style] if slow and style in SLOW_RECIPES else RECIPES[style]


def loop_seconds(style: str, slow: bool = False) -> float:
    r = _recipe(style, slow)
    if style == "ambient":
        return len(r["chords"]) * r["bar"]
    if style == "piano":
        return r["rounds"] * len(r["chords"]) * r["bar"]
    if style in ("lofi", "jazz", "bossa"):
        return r["rounds"] * len(r["chords"]) * 4 * 60.0 / r["bpm"]
    return r["seconds"]


def _n(style: str, r: Optional[dict] = None) -> int:
    if r is not None and "bpm" in r:
        return int(round(RATE * r["rounds"] * len(r["chords"]) * 4 * 60.0 / r["bpm"]))
    return int(round(RATE * loop_seconds(style)))


def _ambient(r: dict) -> list[float]:
    rng = random.Random(r["seed"])
    n = _n("ambient")
    s = [0.0] * n
    t = 0.0
    for ch in r["chords"]:
        freqs = [B._hz(m) for m in ch]
        # Each pad swells over its neighbours: they overlap by half a bar.
        _mix(s, _swell_pad(freqs, r["bar"] * 1.6, rng), t - r["bar"] * 0.3, 0.9)
        for _ in range(r["bells"]):
            m = rng.choice(ch) + rng.choice((12, 24))
            _mix(s, _bell(B._hz(m), 5.0), t + rng.uniform(0.5, r["bar"] - 1.0), 0.12)
        t += r["bar"]
    # A thin shimmer of air above it, so it is never only mid-range hum.
    air = _lp(_hp(_noise(n + RATE, rng), 2500), 7000)
    for i, v in enumerate(_seamless(air, n)):
        s[i] += 0.02 * v
    return s


def _piano(r: dict) -> list[float]:
    rng = random.Random(r["seed"])
    n = _n("piano")
    s = [0.0] * n
    bar = r["bar"]
    t = 0.0
    for _ in range(r["rounds"]):
        for root, ch in r["chords"]:
            # The left hand: root and fifth an octave up from the bass, soft.
            _mix(s, _felt(B._hz(root + 12), bar + 1.5, 0.45, 0.8), t, 0.5)
            _mix(s, _felt(B._hz(root + 19), bar + 1.0, 0.35, 0.8), t + 0.03, 0.5)
            for beat in r["pattern"]:
                if rng.random() < 0.25:
                    continue       # a rest now and then, so it breathes
                m = rng.choice(ch) + 12
                _mix(s, _felt(B._hz(m), 3.0, rng.uniform(0.55, 0.85), 1.0),
                     t + beat * bar / 4 + rng.uniform(0, 0.04), 0.5)
            t += bar
    return s


def _lofi(r: dict) -> list[float]:
    rng = random.Random(r["seed"])
    slow = bool(r.get("slow"))
    n = _n("lofi", r)
    s = [0.0] * n
    beat = 60.0 / r["bpm"]
    bar = 4 * beat
    swing = beat * 0.17          # the off-beat late, for the lazy feel
    drums = [0.0] * n
    t = 0.0
    for _ in range(r["rounds"]):
        for root, ch in r["chords"]:
            for m in ch:
                _mix(s, _rhodes(B._hz(m), bar * 0.9, 0.5), t + rng.uniform(0, 0.02), 0.45)
            if not slow and rng.random() < 0.5:
                # A push on the and of two: the top of the chord again.
                for m in ch[-2:]:
                    _mix(s, _rhodes(B._hz(m), beat * 1.2, 0.35),
                         t + 1.5 * beat + swing, 0.4)
            _mix(s, _pluck_bass(B._hz(root), bar * 0.8), t, 0.25)
            if slow:
                # A soft kick on one and a brush on three: a pulse, no groove.
                _mix(drums, _kick(0.3), t, 1.0)
                _mix(drums, _brush(0.35, 0.3, rng), t + 2 * beat, 1.0)
                t += bar
                continue
            _mix(drums, _kick(0.5), t, 1.0)
            _mix(drums, _kick(0.32), t + 2.5 * beat + swing, 1.0)
            for b in (1, 3):
                _mix(drums, _brush(0.28, 0.6, rng), t + b * beat, 1.0)
            for e in range(8):
                at = t + e * beat / 2 + (swing if e % 2 else 0.0)
                _mix(drums, _tick(0.18 if e % 2 else 0.12, rng), at, 1.0)
            t += bar
    # The whole kit through a low-pass, as if from a tape.
    drums = _lp(_lp(drums, 3500), 5000)
    for i, v in enumerate(drums):
        s[i] += 0.55 * v
    # Vinyl: hiss and sparse crackle, a second longer, crossfaded at the seam.
    m = n + RATE
    hiss = _lp(_hp(_noise(m, rng), 1000), 6000)
    crackle = [0.0] * m
    for _ in range(int(m / RATE * 4)):
        i = rng.randrange(m - 40)
        amp = rng.uniform(0.2, 1.0) * rng.choice((-1, 1))
        for k in range(30):
            crackle[i + k] += amp * math.exp(-k / 5)
    crackle = _hp(crackle, 800)
    vinyl = _seamless([r["hiss"] * h + 0.06 * c for h, c in zip(hiss, crackle)], n)
    for i, v in enumerate(vinyl):
        s[i] += v
    return s


def _jazz(r: dict) -> list[float]:
    rng = random.Random(r["seed"])
    slow = bool(r.get("slow"))
    n = _n("jazz", r)
    s = [0.0] * n
    beat = 60.0 / r["bpm"]
    bar = 4 * beat
    off = beat * 2 / 3           # the swung eighth: two thirds through the beat
    chords = r["chords"]
    t = 0.0
    k = 0
    for _ in range(r["rounds"]):
        for idx, (root, ch) in enumerate(chords):
            if slow:
                # One chord held through the bar, the bass on one and three.
                for m in ch:
                    _mix(s, _felt(B._hz(m), bar * 0.9, rng.uniform(0.45, 0.6), 1.0),
                         t + rng.uniform(0, 0.03), 0.42)
                for b, m in ((0, root), (2, root + 7)):
                    _mix(s, _pluck_bass(B._hz(m + 12), beat * 1.8), t + b * beat, 0.2)
                t += bar
                k += 1
                continue
            # Comping: the Charleston (one, and the and of two), varied.
            hits = [(0.0, 0.5), (beat + off, 1.2)] if rng.random() < 0.6 else \
                   [(off, 0.6), (2 * beat + off, 1.0)]
            for at, d in hits:
                for m in ch:
                    _mix(s, _felt(B._hz(m), d + 0.6, rng.uniform(0.5, 0.7), 1.1),
                         t + at + rng.uniform(0, 0.015), 0.42)
            # Walking bass: root, a chord tone, the fifth, then a step into
            # the next bar's root.
            nxt = chords[(idx + 1) % len(chords)][0]
            walk = [root, root + rng.choice((3, 4)), root + 7,
                    nxt + (1 if rng.random() < 0.5 else -1)]
            for b, m in enumerate(walk):
                _mix(s, _pluck_bass(B._hz(m + 12), beat * 0.95), t + b * beat, 0.22)
            # Brushes: a tap on two and four, the ride's spang-a-lang.
            for b in (1, 3):
                _mix(s, _brush(0.22, 0.35, rng, 500, 3500), t + b * beat, 1.0)
            for at in (0.0, beat, beat + off, 2 * beat, 3 * beat, 3 * beat + off):
                _mix(s, _tick(0.12, rng), t + at, 1.0)
            t += bar
            k += 1
    # The circular brush stroke under it all: a swish swelling every beat.
    m = n + RATE
    sw = _lp(_hp(_noise(m, rng), 600), 4000)
    beats_n = beat * RATE
    swish = [v * (0.4 + 0.6 * math.sin(math.pi * ((i % beats_n) / beats_n)) ** 2)
             for i, v in enumerate(sw)]
    for i, v in enumerate(_seamless(swish, n)):
        s[i] += (0.03 if slow else 0.05) * v
    return s


def _bossa(r: dict) -> list[float]:
    rng = random.Random(r["seed"])
    n = _n("bossa", r)
    s = [0.0] * n
    beat = 60.0 / r["bpm"]
    eighth = beat / 2
    bar = 4 * beat
    chords = r["chords"]
    # The bossa's comping, in eighths of a two-bar phrase: on one, the and
    # of two, the and of three; then two, the and of three (the clave's
    # three and two, which the rim follows).
    comp = ((0, 3, 5), (2, 5))
    rim = ((0, 3, 6), (2, 4))
    t = 0.0
    for _ in range(r["rounds"]):
        for idx, (root, ch) in enumerate(chords):
            half = idx % 2
            for e in comp[half]:
                # Fingers on the strings at once, a hair apart, lowest first.
                vel = rng.uniform(0.5, 0.7) * (1.0 if e == 0 else 0.8)
                for j, m in enumerate(ch):
                    _mix(s, _nylon(B._hz(m), 1.6, vel, seed=idx * 31 + e * 7 + j),
                         t + e * eighth + j * 0.008 + rng.uniform(0, 0.01), 0.3)
            # The bass: root on one, the fifth on three, an octave up so a
            # phone hears it; quiet.
            for b, m in ((0, root), (2, root + 7)):
                _mix(s, _pluck_bass(B._hz(m + 12), beat * 1.6), t + b * beat, 0.2)
            for e in rim[half]:
                _mix(s, _rim(0.25, rng), t + e * eighth, 1.0)
            t += bar
    return s


def _nature(r: dict) -> list[float]:
    rng = random.Random(r["seed"])
    n = _n("nature")
    m = n + RATE
    s = [0.0] * n
    # Rain: music_bed's own, a second longer and crossfaded.
    for i, v in enumerate(_seamless(B._rain(m, seed=r["seed"]), n)):
        s[i] += 0.55 * v
    # A stream: band-limited noise whose level wanders on slow curves that
    # each go a whole number of times round the loop (so they meet at the
    # seam), with bubbles over it.
    water = _lp(_hp(_noise(m, rng), 350), 1800)
    curves = [(rng.randint(2, 7), rng.uniform(0, TAU)) for _ in range(3)]
    for i in range(m):
        lvl = 0.6 + sum(0.13 * math.sin(TAU * c * i / n + p) for c, p in curves)
        water[i] *= lvl
    for i, v in enumerate(_seamless(water, n)):
        s[i] += 0.5 * v
    for _ in range(int(loop_seconds("nature") * 6)):
        d = rng.uniform(0.02, 0.06)
        f0 = rng.uniform(450, 1300)
        k = int(RATE * d)
        ph = 0.0
        bub = []
        for i in range(k):
            ph += TAU * f0 * (1 + 0.6 * i / k) / RATE
            bub.append(math.sin(math.pi * i / k) * math.sin(ph))
        _mix(s, bub, rng.uniform(0, loop_seconds("nature")), 0.05)
    # Birds, now and then, near and far.
    for _ in range(r["birds"]):
        _mix(s, _chirp(rng), rng.uniform(0, loop_seconds("nature")),
             rng.uniform(0.08, 0.2))
    return s


_RENDER: dict[str, Callable[[dict], list[float]]] = {
    "ambient": _ambient, "piano": _piano, "lofi": _lofi, "jazz": _jazz,
    "bossa": _bossa, "nature": _nature}


def render(style: str, slow: bool = False) -> list[float]:
    """One seamless loop of `style` (its slow version with `slow`, where it
    has one), about -1..1 before music_bed scales it."""
    return _RENDER[style](_recipe(style, slow))
