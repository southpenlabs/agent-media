"""This host's DJ mix of a music bed under the voice (music_bed_mix; David,
31 Oct 2026).

A bed on this host's own output (the desk, the rooms) plays on a second mpv
beside the speech broker (sinks/speech.py `SinkSpeech.play_under`), with its
own IPC socket. It is started with three labelled lavfi filters:

- ``@ride`` (volume): the level offset (`gain_db`, the music level applied
  live) and the breathing — `Bed`'s rider polls the speech broker every
  RIDE_STEP_S, takes a clip that is playing (not paused, not idle, not in its
  last TAIL_TRIM_S) as the voice sounding, and moves an envelope 0..1 up in
  attack_ms, down in release_ms once the voice has been quiet hold_ms;
- ``@carve`` (equalizer): a peaking cut in the voice band, its depth the
  same envelope times carve_db;
- ``@sweep`` (lowpass): the cutoff following the fade's level, from
  sweep_hz at silence to open at full.

Each is moved by ``af-command`` (tested on mpv 0.40: the target is the
ffmpeg filter's own name), in steps small enough not to click.

The ending (`end_fade`): the bed's ending clip once, on a third mpv at the
bed's level, while the loop fades in outro_ms; without one, the usual tail
and fade with the sweep closing.

Held between readings (`hold`): a reading that ends with another about to
start leaves its bed playing and writes it to ``under/held-<target>.json``
in the state dir (socket, pid, clip, mix, level, the window's end). A
watcher thread (not a daemon: the process stays until it is done) plays the
ending when the window passes untaken. The next reading — often another
process — takes the file (`take_held`, an atomic rename): the same clip
carries on (adopted: no new player, no intro); a different bed crossfades;
a tone (the thinking harp) puts it back paused and thought_db quieter,
until that reading's end re-arms or lets it go (music_bed_mix.at_end).
"""

from __future__ import annotations

import json
import logging
import math
import os
import signal
import threading
import time
from pathlib import Path
from typing import Callable, Optional

log = logging.getLogger(__name__)

RIDE_STEP_S = 0.03
#: The end of a clip counted as its trailing silence, not voice.
TAIL_TRIM_S = 0.12
OPEN_HZ = 20000.0
#: A held bed paused under a thought longer than this is let go anyway.
PAUSED_CAP_S = 600.0


def _ipc():
    from .sinks import _mpv_ipc
    return _mpv_ipc


def _db(x: float) -> float:
    return 10 ** (x / 20.0)


def carve_band(mix: dict) -> tuple[float, float]:
    """(centre Hz, width in octaves) of the voice-band cut."""
    lo = max(20.0, float(mix.get("carve_lo_hz") or 300.0))
    hi = max(lo * 1.01, float(mix.get("carve_hi_hz") or 3000.0))
    return math.sqrt(lo * hi), math.log2(hi / lo)


def cutoff(sweep_hz: float, level: float) -> float:
    """The sweep's low-pass cutoff at fade level 0..1: exponential from
    `sweep_hz` to OPEN_HZ."""
    if sweep_hz <= 0:
        return OPEN_HZ
    level = min(1.0, max(0.0, level))
    return sweep_hz * (OPEN_HZ / sweep_hz) ** level


def af_arg(mix: dict) -> str:
    """mpv's --af for a bed: the ride, and the carve and the sweep when on,
    starting where a fade in starts (the sweep closed)."""
    parts = [f"@ride:lavfi-volume=volume={_db(float(mix.get('gain_db') or 0.0)):.4f}"]
    if float(mix.get("carve_db") or 0) > 0:
        f, w = carve_band(mix)
        parts.append(f"@carve:lavfi-equalizer=f={f:.0f}:t=o:w={w:.2f}:g=0")
    sweep = float(mix.get("sweep_hz") or 0)
    if sweep > 0:
        parts.append(f"@sweep:lavfi-lowpass=f={sweep:.0f}")
    return "--af=" + ",".join(parts)


class Envelope:
    """The voice envelope: 0 (no voice) .. 1 (speaking). Up in attack_ms;
    down in release_ms, only once the voice has been quiet hold_ms. Linear
    per ms, so in dB the dip is a straight ramp."""

    def __init__(self, attack_ms: float, release_ms: float, hold_ms: float):
        self.attack = max(1.0, float(attack_ms))
        self.release = max(1.0, float(release_ms))
        self.hold = max(0.0, float(hold_ms))
        self.value = 0.0
        self.last_voice: Optional[float] = None

    def step(self, voiced: bool, now: float, dt: float) -> float:
        if voiced:
            self.last_voice = now
        on = voiced or (self.last_voice is not None
                        and (now - self.last_voice) * 1000.0 < self.hold)
        ms = dt * 1000.0
        if on:
            self.value = min(1.0, self.value + ms / self.attack)
        else:
            self.value = max(0.0, self.value - ms / self.release)
        return self.value


def voiced(props: dict) -> bool:
    """Whether the speech broker is sounding a clip, from its properties."""
    if props.get("idle-active") or props.get("pause") or props.get("core-idle"):
        return False
    tp = props.get("time-pos")
    if not isinstance(tp, (int, float)):
        return False
    d = props.get("duration")
    if isinstance(d, (int, float)) and d > 0 and tp >= d - TAIL_TRIM_S:
        return False
    return True


class Bed:
    """The mix on one under mpv: its socket, the mix, and the rider."""

    def __init__(self, sock, mix: dict, broker=None, *, uri: str = "",
                 argv: Optional[list] = None, adopted: bool = False):
        self.sock = str(sock)
        self.mix = dict(mix)
        self.broker = broker
        self.uri = uri
        self.argv = list(argv or [])
        self.adopted = adopted
        self.offset_db = float(mix.get("gain_db") or 0.0)
        self.extra_db = 0.0
        self.env = Envelope(mix.get("attack_ms", 80), mix.get("release_ms", 400),
                            mix.get("hold_ms", 250))
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._sent: dict = {}

    # ---- the filters -------------------------------------------------------

    def _af(self, label: str, filt: str, cmd: str, value: float, eps: float) -> None:
        if abs(self._sent.get(label, 1e9) - value) < eps:
            return
        try:
            _ipc().command(self.sock, "af-command", label, cmd, f"{value:.4f}", filt,
                           timeout=0.3, retry_errors=False)
            self._sent[label] = value
        except Exception:  # noqa: BLE001 — a filter step must never cost the bed
            pass

    def apply(self, amount: float) -> None:
        """The ride and the carve for envelope `amount` (0..1)."""
        ride = self.offset_db - self.extra_db - float(self.mix.get("breathe_db") or 0) * amount
        self._af("ride", "volume", "volume", _db(ride), 0.002)
        carve = float(self.mix.get("carve_db") or 0)
        if carve > 0:
            self._af("carve", "equalizer", "g", -carve * amount, 0.05)

    def on_level(self, frac: float) -> None:
        """A fade step: the sweep follows its level (0..1)."""
        sweep = float(self.mix.get("sweep_hz") or 0)
        if sweep > 0:
            f = cutoff(sweep, frac)
            self._af("sweep", "lowpass", "f", f, max(1.0, f * 0.01))

    # ---- the rider ------------------------------------------------------

    def ride(self) -> None:
        """Start riding the voice (breathe and carve on, a broker to watch)."""
        if self.broker is None or self._thread is not None:
            return
        if not (float(self.mix.get("breathe_db") or 0) > 0
                or float(self.mix.get("carve_db") or 0) > 0):
            return
        self._thread = threading.Thread(target=self._run, name="under-ride", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        names = ["idle-active", "pause", "core-idle", "time-pos", "duration"]
        last = time.monotonic()
        while not self._stop.is_set():
            now = time.monotonic()
            try:
                props = _ipc().get_properties(self.broker, names, timeout=0.3)
            except Exception:  # noqa: BLE001
                props = {}
            a = self.env.step(voiced(props), now, now - last)
            last = now
            self.apply(a)
            self._stop.wait(RIDE_STEP_S)

    def stop(self, settle: bool = True) -> None:
        """Stop riding; `settle` puts the music back to its level between
        words (the envelope at 0)."""
        self._stop.set()
        if settle:
            self.apply(0.0)

    def duck(self, extra_db: float) -> None:
        """Lower (or, with 0, restore) the bed beyond its ride: under a thought."""
        self.extra_db = max(0.0, float(extra_db))
        self.apply(0.0)


# ---- the ending ----------------------------------------------------------------------------

def _outro(bed: Bed, level: float) -> None:
    """The bed's ending, once, at `level` (mpv volume), the ride at its
    offset and the sweep open. Never raises."""
    path = bed.mix.get("outro") or ""
    if not path or not bed.argv or not Path(path).is_file():
        return
    import subprocess
    argv = [a for a in bed.argv if not a.startswith(("--volume=", "--af=",
                                                     "--input-ipc-server=", "--loop"))]
    argv += [f"--volume={level:g}",
             f"--af=lavfi-volume=volume={_db(bed.offset_db - bed.extra_db):.4f}", path]
    try:
        subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True)
    except OSError as e:
        log.info("under-mix: ending failed: %s", e)


def end_fade(bed: Optional[Bed], set_level: Callable[[float], None], level: float,
             done: Callable[[], None], quick: bool):
    """The Fade that ends a bed: quick on a cut; at a natural end its ending
    played while the loop fades in outro_ms, or (none) the usual tail and
    fade, the sweep closing with it."""
    from . import under_fade
    if bed is not None:
        bed.stop(settle=not quick)
    if quick or bed is None:
        return under_fade.Fade(set_level, level, done, quick=quick)
    if bed.mix.get("outro"):
        _outro(bed, level)
        return under_fade.Fade(set_level, level, done, tail_ms=0,
                               ms=int(float(bed.mix.get("outro_ms") or 800)))
    return under_fade.Fade(set_level, level, done)


# ---- held between readings ---------------------------------------------------------------

def _held_path(target_name: str) -> Path:
    from ._paths import state_dir
    d = state_dir() / "under"
    d.mkdir(parents=True, exist_ok=True)
    return d / f"held-{target_name}.json"


def _alive(pid: int) -> bool:
    from . import procinfo
    return procinfo.alive(pid)


class PidProc:
    """A Popen stand-in for an under mpv another process started."""

    def __init__(self, pid: int):
        self.pid = int(pid)

    def terminate(self) -> None:
        try:
            os.kill(self.pid, signal.SIGTERM)
        except OSError:
            pass

    def poll(self):
        return None if _alive(self.pid) else 0


def write_held(target_name: str, rec: dict) -> None:
    p = _held_path(target_name)
    tmp = p.with_name(f".{p.name}.{os.getpid()}.{threading.get_ident()}")
    tmp.write_text(json.dumps(rec))
    os.replace(tmp, p)


def read_held(target_name: str) -> Optional[dict]:
    try:
        d = json.loads(_held_path(target_name).read_text())
        return d if isinstance(d, dict) else None
    except (OSError, ValueError):
        return None


def take_held(target_name: str) -> Optional[dict]:
    """Claim the held bed for `target_name` (an atomic rename: one taker
    wins), or None: none held, or its player gone."""
    p = _held_path(target_name)
    mine = p.with_name(f".{p.name}.taken.{os.getpid()}.{threading.get_ident()}")
    try:
        os.rename(p, mine)
    except OSError:
        return None
    try:
        d = json.loads(mine.read_text())
    except (OSError, ValueError):
        d = None
    finally:
        mine.unlink(missing_ok=True)
    if not isinstance(d, dict):
        return None
    if not _alive(int(d.get("pid") or 0)) or not Path(str(d.get("sock") or "")).exists():
        return None
    return d


def held_record(sock, pid: int, bed: Bed, vol: float, level: float,
                window_ms: int) -> dict:
    return {"sock": str(sock), "pid": int(pid), "uri": bed.uri, "mix": bed.mix,
            "argv": bed.argv, "vol": float(vol), "level": float(level),
            "until": time.time() + window_ms / 1000.0, "paused": False,
            "paused_at": None, "token": f"{os.getpid()}.{time.monotonic_ns()}"}


def bed_of(held: dict, broker=None) -> Bed:
    return Bed(held["sock"], held.get("mix") or {}, broker, uri=held.get("uri") or "",
               argv=held.get("argv") or [], adopted=True)


def let_go(held: dict, quick: bool = False, fade_ms: Optional[int] = None) -> None:
    """End a held bed taken with take_held: its ending (or a quick fade),
    or a fade of `fade_ms` (a crossfade). The fade's thread is not a daemon."""
    from . import under_fade
    bed = bed_of(held)
    proc = PidProc(int(held["pid"]))
    level = float(held.get("level") or held.get("vol") or 0.0)
    vol = float(held.get("vol") or 100.0) or 100.0

    def set_level(v: float) -> None:
        _ipc().command(bed.sock, "set_property", "volume", round(float(v), 2),
                       timeout=0.5, retry_errors=False)
        bed.on_level(v / vol)

    def done() -> None:
        proc.terminate()
        try:
            Path(bed.sock).unlink(missing_ok=True)
        except OSError:
            pass
    if fade_ms is not None:
        fade = under_fade.Fade(set_level, level, done, tail_ms=0, ms=fade_ms)
    else:
        fade = end_fade(bed, set_level, level, done, quick)
    fade.run_async()


def watch(target_name: str, token: str, poll_s: float = 0.25) -> threading.Thread:
    """The holder's watch: when the window passes with the bed untaken (and
    not paused under a thought), take it and play its ending."""

    def run() -> None:
        while True:
            time.sleep(poll_s)
            d = read_held(target_name)
            if d is None or d.get("token") != token:
                return               # taken: carried on, crossfaded or let go
            now = time.time()
            if d.get("paused"):
                if now - float(d.get("paused_at") or now) < PAUSED_CAP_S:
                    continue
            elif now < float(d.get("until") or 0):
                continue
            got = take_held(target_name)
            if got is not None and got.get("token") == token:
                let_go(got)
            elif got is not None:
                write_held(target_name, got)   # someone else's: put it back
            return

    th = threading.Thread(target=run, name="under-held", daemon=False)
    th.start()
    return th
