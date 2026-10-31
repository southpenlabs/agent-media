"""Ducking the way a DJ would: fades, and a depth that fits what is said.

The plain duck is one step down before the first word and one step up after
the last, the same for a two-word notice as for a two-minute answer. A DJ
rides the fader instead: the music slides under the voice as it starts and
comes back up after a beat, a station ident barely dips it, and a long talk
stops it altogether. This module decides that ("the cue") and moves the
volume ("the fader"); the coordinator still owns what is ducked and the debt
that says how to put it back.

Off unless ``MEDIA_DJ_DUCK`` is set (David, 20 Oct 2026: "instead of just
plain ducking … it works more like a DJ would"). Off means the coordinator
behaves exactly as before.

**The depth follows the backing music level** (David, 31 Oct 2026:
"ducking follows the backing music slider"). Every DUCK level — the cue's
talk, short, ambient and urgent, and the plain duck's policy level when the
DJ is off — is multiplied by ``music_bed.duck_scale()``, 10 ** ((gain_db -
DEFAULT_GAIN_DB) / 20), rounded and held to 1..100 (`scaled`). At the
default level (-4 dB) the scale is exactly 1 and every level is as before
(talk 10, short 25, ambient 4); at -12 dB it is about 0.4 (talk 4, short
10, ambient 2); at +6 dB about 3.2 (talk 32). Approximate: a duck level is
a volume, and tracks differ in loudness, so the same slider is not the same
dB over every song. The stop (PAUSE) for longform, a question and a long
reply is not touched, and an explicit ``MEDIA_DUCK_VOLUME`` (or legacy
``AAR_MOPIDY_DUCK_VOLUME``) still fixes the level.

**Breathing.** With music_bed_mix's ``breathe`` switch on, ducked music
swells in the voice's pauses as a bed does: ``MEDIA_MUSIC_BED_BREATHE_DB``
(6) above the duck level, down in ``_ATTACK_MS`` (80), up in
``_RELEASE_MS`` (400) once the voice has been quiet ``_HOLD_MS`` (250). A
player that fades itself is told the swell on the wire (`Cue.wire`'s
``swell``, a share of the music's own volume like ``level``); this host
rides Mopidy and the rooms itself (`Rider`, watching the speech broker as
under_mix's bed rider does), a volume write only when the voice starts or
stops, and never before the first word.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Callable, Optional

from ..types import ContentType
from .policy import InterruptionStrategy, policy_for


def enabled() -> bool:
    """Is DJ-style ducking switched on (``MEDIA_DJ_DUCK``)?"""
    return (os.environ.get("MEDIA_DJ_DUCK") or "").strip().lower() in (
        "1", "true", "yes", "on")


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name) or default)
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name) or default)
    except ValueError:
        return default


def duck_scale() -> float:
    """music_bed.duck_scale(): 1.0 at the default backing music level, and
    on any failure."""
    try:
        from .. import music_bed
        return float(music_bed.duck_scale())
    except Exception:  # noqa: BLE001 — a duck must never cost a reply
        return 1.0


def scaled(level: int, scale: Optional[float] = None) -> int:
    """A duck level moved by the backing music level (`duck_scale`, or
    ``scale``): rounded, never under 1 nor over 100."""
    s = duck_scale() if scale is None else float(scale)
    return max(1, min(100, int(round(level * s))))


def breathe_db() -> float:
    """How far ducked music swells in the voice's pauses: music_bed_mix's
    ``MEDIA_MUSIC_BED_BREATHE_DB`` (6) with its ``breathe`` switch on, else
    0. Never raises."""
    try:
        from .. import music_bed_mix as mix
        return float(mix.tunable("BREATHE_DB")) if mix.on("breathe") else 0.0
    except Exception:  # noqa: BLE001
        return 0.0


def ride_times() -> tuple[float, float, float]:
    """(attack, release, hold) ms of the breathing: music_bed_mix's."""
    try:
        from .. import music_bed_mix as mix
        return (mix.tunable("ATTACK_MS"), mix.tunable("RELEASE_MS"),
                mix.tunable("HOLD_MS"))
    except Exception:  # noqa: BLE001
        return 80.0, 400.0, 250.0


def swell_level(level: float, breathe: float, top: float = 100) -> int:
    """The duck level ``level`` raised by ``breathe`` dB, no higher than
    ``top`` (where the music was before the duck); ``level`` with no
    breathing."""
    if breathe <= 0:
        return int(level)
    return int(max(level, min(top, round(level * 10 ** (breathe / 20.0)))))


def estimate_seconds(text: str) -> Optional[float]:
    """How long ``text`` takes to say, by the device renderer's own rate.

    A guess made before anything is rendered, which is when the cue has to be
    chosen. None for no text: an unknown length is not a short one.
    """
    text = (text or "").strip()
    if not text:
        return None
    from ..render.device import CHARS_PER_S, LEAD_S
    return LEAD_S + len(text) / CHARS_PER_S


@dataclass(frozen=True)
class Cue:
    """What to do to the music for one reply, decided before it starts."""

    strategy: InterruptionStrategy
    level: int              # duck level; 0 for a pause (faded out first)
    fade_down_ms: int
    fade_up_ms: int
    beat_ms: int            # silence after the last word before the fade up
    reason: str             # one word for the log and the now-playing row

    def wire(self, breathe: Optional[float] = None) -> str:
        """The cue for a player that fades itself (Sasonica's DjCue): the
        level as a share of the music's own volume, the times in ms, and
        ``swell``: the level in the voice's pauses, the same kind of share —
        min(1, level/100 * 10 ** (breathe_db / 20)) with breathing on
        (``breathe`` dB, else `breathe_db()`), the level itself with it off,
        0 for a stop."""
        stop = self.strategy == InterruptionStrategy.PAUSE
        b = breathe_db() if breathe is None else float(breathe)
        level = 0.0 if stop else self.level / 100
        swell = min(1.0, level * 10 ** (b / 20.0)) if b > 0 else level
        return (f"level={level:.2f};"
                f"down={self.fade_down_ms};up={self.fade_up_ms};"
                f"beat={self.beat_ms};stop={1 if stop else 0};swell={swell:.2f}")

    def as_extras(self) -> dict:
        return {"cue": self.reason, "fade_down_ms": self.fade_down_ms,
                "fade_up_ms": self.fade_up_ms, "beat_ms": self.beat_ms}


def plan_cue(content_type: Optional[ContentType], *, priority: str = "",
             ask: bool = False, est_s: Optional[float] = None,
             scale: Optional[float] = None) -> Cue:
    """Choose the cue for a reply over music of ``content_type``.

    Pure: everything it needs is passed in, so the rules can be read and
    tested as a table.

    - Longform (book, podcast) pauses as it always has: you cannot half-hear
      narration, and its lead-in rewind is what makes the resume work.
    - Ambient sinks deeper and moves slower: it is a bed, not a song.
    - A short notice (under ``MEDIA_DJ_SHORT_S``, 4 s) only dips the music.
    - A question, or a long reply (over ``MEDIA_DJ_LONG_S``, 45 s), stops the
      music: one wants an answer, the other is a talk, not a voice-over. A DJ
      set is never stopped — pausing a mix breaks it.
    - Urgent comes in fast.
    - Anything else is today's duck, with fades.

    Every DUCK level is `scaled` by the backing music level (``scale``,
    else `duck_scale()`, which reads the settings file); a stop is not.
    """
    ct = content_type or ContentType.UNKNOWN
    base = policy_for(ct)
    down = _env_int("MEDIA_DJ_FADE_DOWN_MS", 400)
    up = _env_int("MEDIA_DJ_FADE_UP_MS", 1500)
    beat = _env_int("MEDIA_DJ_BEAT_MS", 300)
    if priority == "urgent":
        down = min(down, 150)

    if base.strategy == InterruptionStrategy.PAUSE:
        return Cue(InterruptionStrategy.PAUSE, 0, 0, 0, 0, "longform")
    if ct == ContentType.AMBIENT:
        return Cue(InterruptionStrategy.DUCK, scaled(base.duck_level, scale),
                   max(down, 800), max(up, 2500), beat, "ambient")

    long_s = _env_float("MEDIA_DJ_LONG_S", 45.0)
    short_s = _env_float("MEDIA_DJ_SHORT_S", 4.0)
    if ct != ContentType.DJ_SET:
        if ask:
            return Cue(InterruptionStrategy.PAUSE, 0, down, up, beat, "question")
        if est_s is not None and est_s >= long_s:
            return Cue(InterruptionStrategy.PAUSE, 0, down, up, beat, "long")
    if (est_s is not None and est_s < short_s and priority != "urgent"):
        return Cue(InterruptionStrategy.DUCK,
                   scaled(_env_int("MEDIA_DJ_DIP_LEVEL", 25), scale),
                   down, up, beat, "short")
    return Cue(InterruptionStrategy.DUCK, scaled(base.duck_level, scale),
               down, up, beat,
               "urgent" if priority == "urgent" else "talk")


def _curve(t: float) -> float:
    """Smoothstep: eases in and out, so neither end of the fade is a jump."""
    return t * t * (3 - 2 * t)


def fade(set_volume: Callable[[int], None], start: int, end: int, ms: int, *,
         step_ms: int = 40, sleep: Callable[[float], None] = time.sleep) -> None:
    """Move a volume from ``start`` to ``end`` over ``ms``, one setting per
    ``step_ms``, always finishing exactly on ``end``.

    Paced by the clock, not by counting sleeps: a step that is slow to land (a
    round trip to Mopidy or the snapserver) is not added to the fade, the next
    one is simply further along. Blocking, so a caller that has to be quick
    must not ask for a long fade.
    """
    start, end = int(start), int(end)
    if ms <= 0 or start == end:
        set_volume(end)
        return
    began = time.monotonic()
    last: Optional[int] = None
    while True:
        t = (time.monotonic() - began) * 1000.0 / ms
        if t >= 1.0:
            break
        v = int(round(start + (end - start) * _curve(t)))
        if v != last:
            set_volume(v)
            last = v
        sleep(step_ms / 1000.0)
    set_volume(end)


# ---- breathing: the duck ridden as the voice sounds -------------------------

def ride_level(voiced: bool, last_voice: Optional[float], now: float,
               hold_ms: float, duck: int, swell: int) -> int:
    """Where ducked music belongs now: at ``duck`` while the voice sounds
    and until it has been quiet ``hold_ms`` (so the gaps between words never
    pump), and before the first word (``last_voice`` None: the duck was
    just made for it); at ``swell`` in a pause. Pure."""
    if voiced or last_voice is None:
        return int(duck)
    if (now - last_voice) * 1000.0 < hold_ms:
        return int(duck)
    return int(swell)


class _Cut(Exception):
    """A move given up: the voice came back, or the ride was stopped."""


class Rider:
    """Ducked music swelling in the voice's pauses (David, 31 Oct 2026:
    "ducking follows the backing music slider", with the bed's breathing).

    Each *lane* is one volume (Mopidy's, the rooms' clients) with its duck
    and swell levels. A thread polls the speech broker as under_mix's bed
    rider does (`under_mix.voiced`), and on a change of state only — the
    voice starting, or quiet for ``hold_ms`` — moves every lane together
    with `fade`: down in ``attack_ms``, up in ``release_ms``. A swell cut
    short by the voice turns straight back down from where it got to. A
    volume write is made only when a lane's value changes, so Mopidy sees a
    handful per pause, not a stream.

    ``props`` (for tests) replaces the broker read: a callable returning
    mpv's properties. ``stop()`` ends the ride where it is and returns each
    lane's level, for the restore to fade up from.
    """

    STEP_S = 0.03

    def __init__(self, broker=None, *, attack_ms: float = 80.0,
                 release_ms: float = 400.0, hold_ms: float = 250.0,
                 props: Optional[Callable[[], dict]] = None,
                 step_ms: int = 60) -> None:
        import threading
        self.broker = broker
        self.attack_ms = max(1.0, float(attack_ms))
        self.release_ms = max(1.0, float(release_ms))
        self.hold_ms = max(0.0, float(hold_ms))
        self.step_ms = int(step_ms)
        self._props = props
        self._lock = threading.Lock()
        # name -> [set_level, duck, swell, current]
        self._lanes: dict = {}
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self.last_voice: Optional[float] = None
        self.state = "duck"

    def add(self, name: str, set_level: Callable[[int], None], duck: int,
            swell: int, current: Optional[int] = None) -> None:
        """Ride one more volume, at ``duck`` now (``current`` if known)."""
        with self._lock:
            self._lanes[name] = [set_level, int(duck), int(swell),
                                 int(duck if current is None else current)]

    def levels(self) -> dict:
        with self._lock:
            return {n: lane[3] for n, lane in self._lanes.items()}

    def start(self) -> None:
        import threading
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, name="duck-ride",
                                        daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 2.0) -> dict:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout)
        return self.levels()

    # ---- the voice ---------------------------------------------------------

    def _read(self) -> dict:
        if self._props is not None:
            try:
                return self._props() or {}
            except Exception:  # noqa: BLE001
                return {}
        try:
            from ..sinks import _mpv_ipc
            return _mpv_ipc.get_properties(
                self.broker, ["idle-active", "pause", "core-idle", "time-pos",
                              "duration"], timeout=0.3) or {}
        except Exception:  # noqa: BLE001
            return {}

    def _voiced(self) -> bool:
        from .. import under_mix
        return under_mix.voiced(self._read())

    def target(self, voiced: bool, now: float) -> str:
        """"duck" or "swell", noting the voice when it sounds."""
        if voiced:
            self.last_voice = now
        return "swell" if ride_level(voiced, self.last_voice, now, self.hold_ms,
                                     0, 1) == 1 else "duck"

    # ---- the moves ---------------------------------------------------------

    def _set(self, lane: list, v: int) -> None:
        if lane[3] == v:
            return
        try:
            lane[0](v)
        except Exception:  # noqa: BLE001 — a step must never cost the reply
            return
        lane[3] = v

    def move(self, to: str, sleep: Optional[Callable[[float], None]] = None) -> None:
        """Every lane to its ``to`` level ("duck" or "swell") together."""
        with self._lock:
            lanes = list(self._lanes.values())
        idx = 1 if to == "duck" else 2
        moves = [(lane, lane[3], lane[idx]) for lane in lanes]
        ms = self.attack_ms if to == "duck" else self.release_ms

        def at(permille: int) -> None:
            for lane, start, end in moves:
                self._set(lane, int(round(start + (end - start) * permille / 1000)))
        fade(at, 0, 1000, int(ms), step_ms=self.step_ms,
             sleep=sleep or self._stop.wait)
        self.state = to

    def _run(self) -> None:
        while not self._stop.is_set():
            now = time.monotonic()
            want = self.target(self._voiced(), now)
            if want != self.state:
                def sleep(s: float, want=want) -> None:
                    if self._stop.wait(s):
                        raise _Cut()
                    # A swell gives way at once to the voice coming back.
                    if want == "swell" and self._voiced():
                        self.last_voice = time.monotonic()
                        raise _Cut()
                try:
                    self.move(want, sleep)
                except _Cut:
                    self.state = "between"
                continue
            self._stop.wait(self.STEP_S)


__all__ = ["Cue", "Rider", "breathe_db", "duck_scale", "enabled",
           "estimate_seconds", "fade", "plan_cue", "ride_level", "scaled",
           "swell_level"]
