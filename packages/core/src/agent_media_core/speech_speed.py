"""Whether the speech rate goes back to 1× when a reply ends.

The rate lives on the player and outlives a reply (cli.py `cmd_speed`,
StateStore `speech_speed`), so 1.5× set on one reply is still 1.5× on the
next. That stays the default. With the reset on, every spoken reply (and
every replay) that ends puts the player back at 1× and writes 1.0 down, so
the next one starts at normal speed and the bars say 1× in between.

Applied where a reply ends (intake/submit.py, both the live reply and the
remote renderer) and where a replay ends (cli.py `_finish`). Best effort: a
player that cannot be reached keeps its rate, and a press after that starts
from 1.0, because what a press sends is absolute.

Kept in `<state_dir>/speech-speed-reset.json` as `{"reset", "at"}`; the
server's, so every device paired with it (`GET`/`POST /speech/speed-reset`,
the app's Settings and the full player's speed keys). David, 31 Oct 2026:
"a setting for whether or not speed resets after each turn".
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


def _path() -> Path:
    return state_dir() / NAME


def reset_after_reply() -> bool:
    """True when the rate goes back to 1× as each reply ends; off until set."""
    try:
        return bool(json.loads(_path().read_text())["reset"])
    except (OSError, ValueError, TypeError, KeyError):
        return False


def set_reset_after_reply(on: bool) -> bool:
    """Save the choice and return it."""
    on = bool(on)
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f".tmp.{os.getpid()}")
    tmp.write_text(json.dumps({"reset": on, "at": time.time()}))
    tmp.replace(path)
    return on


def at_reply_end(sock) -> bool:
    """A reply (or replay) on `sock` ended: back to 1× if the setting says so.

    Only when the rate written down is not already 1.0, so a listener who
    never touches the speed costs nothing here. True if it reset.
    """
    if not reset_after_reply():
        return False
    try:
        from .state.store import StateStore
        st = StateStore()
        cur = st.get_speech_speed()
        if not isinstance(cur, (int, float)) or abs(float(cur) - 1.0) < 0.005:
            return False
        # Written first, as a press does: the next press ladders off 1.0
        # even if the player is out of reach.
        st.set_speech_speed(1.0)
    except Exception:  # noqa: BLE001 — never cost a reply's cleanup
        log.debug("speed reset: store failed", exc_info=True)
        return False
    try:
        from .sinks import _mpv_ipc as ipc
        ipc.command(sock, "set_property", "speed", 1.0, timeout=2.0,
                    retry_errors=False)
    except Exception:  # noqa: BLE001 — unreachable keeps its rate
        log.debug("speed reset: player not reached", exc_info=True)
    return True
