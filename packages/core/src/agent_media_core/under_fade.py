"""Fading what plays under the voice, in and out (David, 30 Oct 2026: "it
would be nice if it faded in and out rather than just a cold stop start").

Everything looped under a reply — the music beds and the looping under
tones (the thinking harp, Air, the people's harp) — fades in from silence
as it starts, and at the reply's end carries on a moment past the last word
and fades out. A cut by the listener (a Stop, End of reply) or a new reply
taking over fades it quickly instead: never a hard cut.

- MEDIA_UNDER_FADE_IN_MS (1500): the fade in from silence.
- MEDIA_UNDER_TAIL_MS (1000): how long it carries on past the last word.
- MEDIA_UNDER_FADE_OUT_MS (2500): the fade out after that.
- MEDIA_UNDER_QUICK_FADE_MS (500): the fade out on a cut or a takeover.

Where: this host's own under player (sinks/speech.py SinkSpeech.play_under,
a second mpv) is faded by its volume through its own IPC socket (`Fade`);
the streaming bed's Mopidy volume by the DJ fader (music_bed_stream); a
phone's under player by the app itself, with the same times built in.

The curve is the DJ duck's (route/dj.py `_curve`, a smoothstep).
"""

from __future__ import annotations

import logging
import os
import threading
import time
from typing import Callable, Optional

log = logging.getLogger(__name__)

DEFAULT_FADE_IN_MS = 1500
DEFAULT_TAIL_MS = 1000
DEFAULT_FADE_OUT_MS = 2500
DEFAULT_QUICK_MS = 500
STEP_S = 0.04


def _ms(name: str, default: int) -> int:
    try:
        return max(0, int(float(os.environ.get(name, "") or default)))
    except ValueError:
        return default


def fade_in_ms() -> int:
    return _ms("MEDIA_UNDER_FADE_IN_MS", DEFAULT_FADE_IN_MS)


def tail_ms() -> int:
    return _ms("MEDIA_UNDER_TAIL_MS", DEFAULT_TAIL_MS)


def fade_out_ms() -> int:
    return _ms("MEDIA_UNDER_FADE_OUT_MS", DEFAULT_FADE_OUT_MS)


def quick_ms() -> int:
    return _ms("MEDIA_UNDER_QUICK_FADE_MS", DEFAULT_QUICK_MS)


def curve(t: float) -> float:
    """0..1 → 0..1, eased at both ends (smoothstep)."""
    t = min(1.0, max(0.0, t))
    return t * t * (3 - 2 * t)


class Fade:
    """One fade out, run on its own thread: wait out the tail (unless
    quick), ramp `set_level` from `start` to 0, then `done()`. `hurry()`
    turns a fade under way into a quick one (a new reply took over): the
    tail is cut and what is left of the ramp is done in the quick time.
    `tail_ms` and `ms` override the tail and the fade's length (a bed's
    ending, a crossfade: under_mix).

    The thread is not a daemon, so a process that ends just after a reply
    still finishes the fade and stops the player (at most tail + fade)."""

    def __init__(self, set_level: Callable[[float], None], start: float,
                 done: Callable[[], None], quick: bool = False,
                 sleep: Callable[[float], None] = time.sleep,
                 tail_ms: Optional[int] = None, ms: Optional[int] = None):
        self.set_level = set_level
        self.tail_ms = tail_ms
        self.ms = ms
        self.start = float(start)
        self.done = done
        self._hurry = threading.Event()
        if quick:
            self._hurry.set()
        self._sleep = sleep
        self.thread = threading.Thread(target=self._run, name="under-fade-out",
                                       daemon=False)

    def run_async(self) -> "Fade":
        self.thread.start()
        return self

    def hurry(self) -> None:
        self._hurry.set()

    def join(self, timeout: Optional[float] = None) -> None:
        self.thread.join(timeout)

    def _run(self) -> None:
        try:
            tail = tail_ms() if self.tail_ms is None else self.tail_ms
            if not self._hurry.is_set() and tail > 0:
                self._hurry.wait(tail / 1000.0)
            level = self.start
            began = time.monotonic()
            ms = quick_ms() if self._hurry.is_set() else (
                fade_out_ms() if self.ms is None else self.ms)
            top = level
            while ms > 0:
                if self._hurry.is_set() and ms > quick_ms():
                    # Hurried mid-fade: the rest from where it is, quickly.
                    top, began, ms = level, time.monotonic(), quick_ms()
                    if ms <= 0:
                        break
                t = (time.monotonic() - began) * 1000.0 / ms
                if t >= 1.0:
                    break
                level = top * (1.0 - curve(t))
                try:
                    self.set_level(level)
                except Exception:  # noqa: BLE001 — the player went: stop
                    break
                self._sleep(STEP_S)
        finally:
            try:
                self.done()
            except Exception as e:  # noqa: BLE001
                log.debug("under fade: done failed: %s", e)


def fade_in(set_level: Callable[[float], None], end: float,
            ms: Optional[int] = None, ready: Optional[Callable[[], bool]] = None,
            sleep: Callable[[float], None] = time.sleep) -> threading.Thread:
    """Ramp `set_level` from 0 to `end` over `ms` (fade_in_ms) on a daemon
    thread, waiting up to a second for `ready()` first (the player's socket
    coming up). A step that fails is retried on the next; never raises."""
    ms = fade_in_ms() if ms is None else ms

    def run() -> None:
        if ready is not None:
            until = time.monotonic() + 1.0
            while not ready() and time.monotonic() < until:
                sleep(0.02)
        began = time.monotonic()
        while ms > 0:
            t = (time.monotonic() - began) * 1000.0 / ms
            if t >= 1.0:
                break
            try:
                set_level(end * curve(t))
            except Exception:  # noqa: BLE001
                pass
            sleep(STEP_S)
        try:
            set_level(end)
        except Exception:  # noqa: BLE001
            pass

    th = threading.Thread(target=run, name="under-fade-in", daemon=True)
    th.start()
    return th
