"""The radio's loop: agent_media_core.radio.tick, every few seconds.

Started with the canvas (as the search index is) and always running: an off
station is one small file read per pass, and a station started from a shell
(`media music radio`) is picked up without a word to this process. The Media
tab's own start and 👎 wake it at once (:func:`wake`).

The same loop ends a song of a mix played on its own at its chapter's end
(agent_media_core.music_segment), and looks every second or so near a
bounded station item's start or end (radio.wait).
"""

from __future__ import annotations

import sys
import threading

_PERIOD_S = 4.0
_WAKE = threading.Event()
_STARTED: list = [False]


def wake() -> None:
    _WAKE.set()


def _loop() -> None:
    from agent_media_core import radio

    period = _PERIOD_S
    while True:
        _WAKE.wait(period)
        _WAKE.clear()
        try:
            radio.tick(fetch_aside=True)
        except Exception as e:  # noqa: BLE001 — the loop outlives a bad pass
            print(f"radio: tick failed: {e}", file=sys.stderr)
        period = _PERIOD_S
        try:
            period = min(period, radio.wait(_PERIOD_S))
        except Exception:  # noqa: BLE001
            pass
        try:
            seg = _segment()
            if seg is not None:
                period = min(period, seg)
        except Exception as e:  # noqa: BLE001
            print(f"radio: segment failed: {e}", file=sys.stderr)


def _segment():
    """A song of a mix played on its own (agent_media_core.music_segment):
    at its end, the queue's next item or a stop. The seconds until it wants
    another look, or None with none set."""
    from agent_media_core import music_segment

    if music_segment.current() is None:
        return None
    from . import music

    def run(argv: list) -> None:
        music._run(argv)
        music._reset_cache()

    return music_segment.tick(music._status, run)


def start() -> None:
    if _STARTED[0]:
        return
    _STARTED[0] = True
    threading.Thread(target=_loop, name="radio", daemon=True).start()
