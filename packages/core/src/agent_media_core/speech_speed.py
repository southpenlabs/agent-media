"""Where the speech rate starts each reply.

The rate lives on the player and outlives a reply (cli.py `cmd_speed`,
StateStore `speech_speed`), so 1.5× set on one reply is still 1.5× on the
next. That stays the default. The listener can choose (David, 31 Oct and
2 Nov 2026):

- `reset`: every spoken reply (and every replay) that ends puts the player
  back at the start speed and writes it down, so the next one starts there
  and the bars say so in between ("a setting for whether or not speed resets
  after each turn").
- `start`: the speed a reply starts at when it starts afresh, 1× unless set
  ("a start speed other than 1×").
- `per_thread`: each thread keeps the speed it was last heard at; a thread
  heard for the first time starts at the start speed. Switched off, every
  thread's speed is forgotten.
- `break_min`: with the speed carrying between replies, it goes back to the
  start speed when nothing has been spoken for this many minutes (0: never).
- `step`: how far a faster or slower press moves (David, 10 Nov 2026: "a
  setting somewhere to set the speed increments"): 0, the ladder (1, 1.25,
  1.5, 2, 3, and 0.1 steps under 1×), or one of STEPS, even steps all the
  way (`next_speed`, which cli.py `media speed up|down` uses).

Applied where a reply ends (`at_reply_end`: intake/submit.py, both the live
reply and the remote renderer, and cli.py `_finish` for a replay) and where
a live reply starts (`at_reply_start`, intake/submit.py). Best effort: a
player that cannot be reached keeps its rate, and a press after that starts
from what was written down, because what a press sends is absolute.

Kept in `<state_dir>/speech-speed-reset.json` as `{"reset", "start",
"per_thread", "break_min", "step", "at"}`, and what was heard in
`speech-speed-heard.json` (`{"last_end", "threads": {session: rate}}`); the
server's, so every device paired with it (`GET`/`POST /speech/speed-reset`,
the app's Settings and the full player's speed keys).
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path

from ._paths import state_dir

log = logging.getLogger(__name__)

NAME = "speech-speed-reset.json"
HEARD = "speech-speed-heard.json"
#: The rates a start speed may be (cli.py _SPEED_RUNGS, with 1.75×).
STARTS = (1.0, 1.25, 1.5, 1.75, 2.0)
#: The break lengths offered, in minutes (0: never).
BREAKS = (0, 5, 15, 30, 60)
#: The even steps a press may move by (0, not one of them, is the ladder).
STEPS = (0.05, 0.1, 0.25)
#: The ladder at and above 1×, flat steps under it, and the ends (cli.py's).
RUNGS = (1.0, 1.25, 1.5, 2.0, 3.0)
FLAT, MIN, MAX = 0.1, 0.3, 3.0
#: Threads remembered, newest kept.
MAX_THREADS = 200


def _path() -> Path:
    return state_dir() / NAME


def _heard_path() -> Path:
    return state_dir() / HEARD


def _write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f".tmp.{os.getpid()}")
    tmp.write_text(json.dumps(data))
    tmp.replace(path)


def settings() -> dict:
    """`{"reset", "start", "per_thread", "break_min", "step"}`, defaults filled in."""
    try:
        raw = json.loads(_path().read_text())
        if not isinstance(raw, dict):
            raw = {}
    except (OSError, ValueError, TypeError):
        raw = {}
    start = raw.get("start")
    brk = raw.get("break_min")
    step = raw.get("step")
    return {
        "reset": bool(raw.get("reset", False)),
        "start": float(start) if isinstance(start, (int, float)) and float(start) in STARTS else 1.0,
        "per_thread": bool(raw.get("per_thread", False)),
        "break_min": int(brk) if isinstance(brk, (int, float)) and int(brk) in BREAKS else 0,
        "step": float(step) if isinstance(step, (int, float)) and not isinstance(step, bool) and float(step) in STEPS else 0,
    }


def save_settings(**changes) -> dict:
    """Change some of the settings (unknown values refused) and return them all."""
    cur = settings()
    if "reset" in changes and changes["reset"] is not None:
        cur["reset"] = bool(changes["reset"])
    if "start" in changes and changes["start"] is not None:
        v = float(changes["start"])
        if v not in STARTS:
            raise ValueError(f"start must be one of {', '.join(map(str, STARTS))}")
        cur["start"] = v
    if "per_thread" in changes and changes["per_thread"] is not None:
        # Switched off, every thread's speed is forgotten, so switching it on
        # again starts fresh (David, 2 Nov 2026: "Is there a setting for a
        # global reset for threads that keep their speed?" — "Reset when
        # switched off").
        if cur["per_thread"] and not changes["per_thread"]:
            forget_threads()
        cur["per_thread"] = bool(changes["per_thread"])
    if "break_min" in changes and changes["break_min"] is not None:
        v = int(changes["break_min"])
        if v not in BREAKS:
            raise ValueError(f"break_min must be one of {', '.join(map(str, BREAKS))}")
        cur["break_min"] = v
    if "step" in changes and changes["step"] is not None:
        v = float(changes["step"])
        if v != 0 and v not in STEPS:
            raise ValueError(f"step must be 0 or one of {', '.join(map(str, STEPS))}")
        cur["step"] = 0 if v == 0 else v
    _write(_path(), {**cur, "at": time.time()})
    return cur


def next_speed(cur: float, direction: int, step: float | None = None) -> float:
    """The speed a faster (+1) or slower (-1) press goes to from `cur`.

    `step` (the setting when None): 0 climbs the ladder at and above 1× and
    moves FLAT under it; an even step goes to the next multiple of it, so a
    rate off the grid (1.75 with 0.25... or 1.33) lands back on it. Between
    MIN and MAX."""
    if step is None:
        step = settings()["step"]
    eps = 1e-6
    if step:
        n = cur / step
        k = (int(n + eps) + 1) if direction > 0 else (int(n - eps) if abs(n - round(n)) > eps else round(n) - 1)
        return round(max(MIN, min(MAX, k * step)), 2)
    if direction > 0:
        if cur < 1.0 - eps:
            return min(round(cur + FLAT, 2), 1.0)
        for r in RUNGS:
            if r > cur + eps:
                return r
        return MAX
    if cur > 1.0 + eps:
        for r in reversed(RUNGS):
            if r < cur - eps:
                return r
        return 1.0
    return max(round(cur - FLAT, 2), MIN)


def reset_after_reply() -> bool:
    """True when the rate goes back to the start speed as each reply ends; off until set."""
    return settings()["reset"]


def set_reset_after_reply(on: bool) -> bool:
    """Save the choice and return it."""
    return save_settings(reset=bool(on))["reset"]


def forget_threads() -> None:
    """Every thread's remembered speed dropped; when last heard kept."""
    heard = _heard()
    if heard.pop("threads", None) is not None:
        _write(_heard_path(), heard)


def _heard() -> dict:
    try:
        raw = json.loads(_heard_path().read_text())
        return raw if isinstance(raw, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def _set_rate(sock, rate: float) -> bool:
    """Write `rate` down and send it to the player when it is not already that. True if changed."""
    try:
        from .state.store import StateStore
        st = StateStore()
        cur = st.get_speech_speed()
        cur = float(cur) if isinstance(cur, (int, float)) else 1.0
        if abs(cur - rate) < 0.005:
            return False
        # Written first, as a press does: the next press ladders off it
        # even if the player is out of reach.
        st.set_speech_speed(rate)
    except Exception:  # noqa: BLE001 — never cost a reply
        log.debug("speech speed: store failed", exc_info=True)
        return False
    try:
        from .sinks import _mpv_ipc as ipc
        ipc.command(sock, "set_property", "speed", rate, timeout=2.0,
                    retry_errors=False)
    except Exception:  # noqa: BLE001 — unreachable keeps its rate
        log.debug("speech speed: player not reached", exc_info=True)
    return True


def _current_rate() -> float | None:
    try:
        from .state.store import StateStore
        cur = StateStore().get_speech_speed()
        return float(cur) if isinstance(cur, (int, float)) else None
    except Exception:  # noqa: BLE001
        return None


def start_rate(session: str = "", now: float | None = None) -> float | None:
    """The rate a reply in `session` should start at, or None to leave the
    player as it is (the speed carrying on)."""
    s = settings()
    if s["per_thread"] and session:
        rate = (_heard().get("threads") or {}).get(session)
        if isinstance(rate, (int, float)) and 0.25 <= float(rate) <= 4.0:
            return float(rate)
        return s["start"]
    if s["reset"]:
        return s["start"]
    if s["break_min"]:
        last = _heard().get("last_end")
        now = time.time() if now is None else now
        if isinstance(last, (int, float)) and now - float(last) >= s["break_min"] * 60:
            return s["start"]
    return None


def at_reply_start(sock, session: str = "", now: float | None = None) -> bool:
    """A live reply in `session` is about to play on `sock`: start it at the
    chosen speed (its thread's, the start speed after a break, ...). True if
    the rate changed."""
    try:
        rate = start_rate(session, now)
    except Exception:  # noqa: BLE001 — never cost a reply
        log.debug("speech speed: start failed", exc_info=True)
        return False
    return False if rate is None else _set_rate(sock, rate)


def at_reply_end(sock, session: str = "", now: float | None = None) -> bool:
    """A reply (or replay) on `sock` ended: noted (when, and the thread's
    rate when kept per thread), then back to the start speed if the setting
    says so. True if it reset.

    Only when the rate written down is not already the start speed, so a
    listener who never touches the speed costs nothing here.
    """
    s = settings()
    try:
        heard = _heard()
        heard["last_end"] = time.time() if now is None else now
        if s["per_thread"] and session:
            rate = _current_rate()
            threads = dict(heard.get("threads") or {})
            threads.pop(session, None)
            threads[session] = rate if rate is not None else s["start"]
            while len(threads) > MAX_THREADS:
                threads.pop(next(iter(threads)))
            heard["threads"] = threads
        _write(_heard_path(), heard)
    except Exception:  # noqa: BLE001 — never cost a reply's cleanup
        log.debug("speech speed: heard not noted", exc_info=True)
    if not s["reset"]:
        return False
    return _set_rate(sock, s["start"])
