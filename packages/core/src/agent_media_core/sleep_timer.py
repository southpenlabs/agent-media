"""A sleep timer: the music and the book channel fade out and stop at a set
time (docs/proposals/2026-10-31-relax-and-meditate.md §4; David, 1 Nov 2026:
"yeah, let's do it").

`start(minutes, fade_s)` records the timer in ``<state_dir>/sleep-timer.json``
and starts a detached worker (``python -m agent_media_core.sleep_timer run``)
that outlives the caller. Over the last `fade_s` (MEDIA_SLEEP_FADE_S, 180) it
turns the music down from where it was to nothing, and a book on this host's
player too; then it stops both (stop, not pause: a pause on a phone in use is
someone's playhead held), and puts the music's volume back so the next play
is not silent. A phone's book player is stopped without a fade. `cancel()`
ends it (the worker sees its id gone and leaves everything as it is); a new
`start` replaces an old one.

Sleep stories and meditations play on the book channel, so a timer ends them
too: "play something calm for 30 minutes" is a play and a timer.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Callable, Optional

from ._paths import state_dir

log = logging.getLogger(__name__)

STEP_S = 5.0


def _path() -> Path:
    return state_dir() / "sleep-timer.json"


def fade_default() -> float:
    try:
        return max(0.0, min(1800.0, float(os.environ.get("MEDIA_SLEEP_FADE_S") or 180)))
    except ValueError:
        return 180.0


def _read() -> Optional[dict]:
    try:
        d = json.loads(_path().read_text())
    except (OSError, ValueError):
        return None
    return d if isinstance(d, dict) and "until" in d else None


def status(now: Optional[float] = None) -> Optional[dict]:
    """The running timer: ``{"id", "until", "fade_s", "target", "left_s"}``, or None."""
    d = _read()
    if d is None:
        return None
    left = float(d["until"]) - (now if now is not None else time.time())
    if left <= -60:
        return None
    return {**d, "left_s": max(0.0, round(left))}


def _spawn(timer_id: str) -> None:
    subprocess.Popen([sys.executable, "-m", "agent_media_core.sleep_timer", "run", timer_id],
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, start_new_session=True)


def start(minutes: float, fade_s: Optional[float] = None, target: str = "",
          spawn: Optional[Callable[[str], None]] = None, now: Optional[float] = None) -> dict:
    """Start (or replace) the timer: everything stops `minutes` from now."""
    if not (0 < float(minutes) <= 24 * 60):
        raise ValueError("minutes must be more than 0 and at most a day")
    fade = fade_default() if fade_s is None else max(0.0, float(fade_s))
    total = float(minutes) * 60
    timer = {"id": uuid.uuid4().hex[:12], "until": (now if now is not None else time.time()) + total,
             "fade_s": min(fade, total), "target": target, "set": time.time()}
    p = _path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(f".tmp.{os.getpid()}")
    tmp.write_text(json.dumps(timer))
    tmp.replace(p)
    (spawn or _spawn)(timer["id"])
    return status(now) or timer


def cancel() -> bool:
    """End the timer; True if one was running."""
    had = _read() is not None
    _path().unlink(missing_ok=True)
    return had


def _mine(timer_id: str) -> bool:
    """Still this worker's timer (not cancelled, not replaced)."""
    d = _read()
    return bool(d) and d.get("id") == timer_id


def run(timer_id: str, sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.time, srv=None) -> str:
    """The worker: wait, fade, stop. Returns what happened ("stopped",
    "cancelled", "gone")."""
    s = status(clock())
    if not s or s.get("id") != timer_id:
        return "gone"
    if srv is None:
        from . import mcp_server as srv
    until, fade, target = float(s["until"]), float(s["fade_s"]), s.get("target") or ""
    while clock() < until - fade:
        sleep(min(STEP_S, max(0.1, until - fade - clock())))
        if not _mine(timer_id):
            return "cancelled"
    music = srv._music()
    mt = srv._music_target(target)
    try:
        start_level = music.current_volume(mt)
    except Exception:  # noqa: BLE001
        start_level = None
    book = srv._book()
    bt = srv._book_target(target)
    try:
        book_level = book.volume(bt)
    except Exception:  # noqa: BLE001
        book_level = None
    while clock() < until:
        if not _mine(timer_id):
            return "cancelled"
        share = max(0.0, (until - clock()) / fade) if fade > 0 else 0.0
        try:
            if start_level:
                music.duck(mt, int(round(start_level * share)))
            if book_level:
                book.set_volume(int(round(book_level * share)), bt)
        except Exception as e:  # noqa: BLE001 — a missed step is not a missed stop
            log.debug("sleep timer: fade: %s", e)
        sleep(min(STEP_S, max(0.1, until - clock())))
    if not _mine(timer_id):
        return "cancelled"
    for name, fn in (("music", lambda: srv.music_stop(target)), ("book", lambda: srv.book_stop(target))):
        try:
            fn()
        except Exception as e:  # noqa: BLE001
            log.info("sleep timer: %s stop: %s", name, e)
    # Back to the levels they had, for whatever plays next.
    try:
        if start_level:
            music.duck(mt, int(start_level))
        if book_level:
            book.set_volume(int(book_level), bt)
    except Exception as e:  # noqa: BLE001
        log.debug("sleep timer: restore: %s", e)
    _path().unlink(missing_ok=True)
    return "stopped"


if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "run":
        from .intake._env import load_env_file
        load_env_file()
        print(run(sys.argv[2]))


PRESETS = (15, 30, 45, 60)


def offer() -> dict:
    """The timer for an app: what is set, and the lengths to offer
    (GET /practice `sleep`)."""
    s = status()
    return {"timer": {"left_s": s["left_s"], "fade_s": s["fade_s"]} if s else None,
            "minutes": list(PRESETS), "fade_s": fade_default()}
