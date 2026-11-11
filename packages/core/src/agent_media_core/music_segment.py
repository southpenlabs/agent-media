"""Play just one song of a mix: stop or move on at its chapter's end.

David, 12 Nov 2026 (mix_songs): a song inside a mix picked on its own plays
from its chapter's start to its end, not on into the rest of the mix.

The start is the play's `--start` (cli._music_start_at: a seek once the
player has the track). The end is kept here, in `<state_dir>/music-segment.
json` (`{"vid", "start", "end", "title", "at"}`), by `media music play
--end`, and enforced by `tick`, which the server's loop runs with the radio
(agent_media_server.radio): a read of the live player (`media music status`),
and at the end the queue's next item, else a stop. No player has to know
about it, so it holds for Sasonica's player (whose socket has no `end`
property), the Termux mpv and Mopidy alike; the price is a read every few
seconds while one is set, closer together near the end, so it stops within
about a second of it.

It lets go (the file removed) when the song ended, when something else has
been playing a while, or when it is much older than the song.
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Callable, Optional

from ._paths import state_dir

log = logging.getLogger(__name__)

#: How long after the play its video may still be loading before something
#: else playing means it was replaced.
_LOAD_GRACE_S = 90.0
#: Stopped this close to the end (a read lands up to a pass late otherwise).
_EARLY_S = 0.4
#: Further past the end than a late look can be: a seek on, not the end.
_PAST_S = 8.0
#: The loop's longest wait while one is set, and its shortest.
_MAX_WAIT_S = 4.0
_MIN_WAIT_S = 0.5


def _path():
    return state_dir() / "music-segment.json"


def current() -> Optional[dict]:
    try:
        d = json.loads(_path().read_text())
    except (OSError, ValueError):
        return None
    if not isinstance(d, dict) or not d.get("vid") or not isinstance(d.get("end"), (int, float)):
        return None
    return d


def set_segment(vid: str, start: float, end: float, title: str = "",
                now: Optional[float] = None) -> dict:
    """From now, `vid` plays to `end` seconds and no further."""
    d = {"vid": vid, "start": float(start or 0), "end": float(end), "title": title or "",
         "at": time.time() if now is None else now}
    p = _path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(f".{p.name}.{os.getpid()}")
    tmp.write_text(json.dumps(d))
    os.replace(tmp, p)
    return d


def clear() -> None:
    try:
        _path().unlink()
    except OSError:
        pass


def tick(status: Callable[[], dict], run: Callable[[list], object],
         now: Optional[float] = None) -> Optional[float]:
    """One look: at the song's end, the queue's next item or a stop. The
    seconds until the next look is wanted, or None when nothing is set.
    `status` is `media music status --json`; `run` runs a `media music` verb."""
    seg = current()
    if seg is None:
        return None
    now = time.time() if now is None else now
    age = now - float(seg.get("at") or 0)
    if age > (seg["end"] - seg.get("start", 0)) + 3 * 3600:
        clear()
        return None
    try:
        st = status() or {}
    except Exception as e:  # noqa: BLE001 — ask again next pass
        log.info("music-segment: status failed: %s", e)
        return _MAX_WAIT_S
    if (st.get("media_id") or "") != seg["vid"] or st.get("pos_ms") is None:
        if age > _LOAD_GRACE_S:
            # Something else is on (or nothing): this song is done with.
            clear()
            return None
        return _MAX_WAIT_S
    pos = st["pos_ms"] / 1000.0
    left = seg["end"] - pos
    if left < -_PAST_S:
        # Moved on past the song on purpose (a seek): the rest is wanted.
        clear()
        return None
    if left <= _EARLY_S:
        clear()
        q = st.get("queue") or {}
        more = isinstance(q, dict) and isinstance(q.get("pos"), int) and isinstance(q.get("count"), int) \
            and q["pos"] + 1 < q["count"]
        log.info("music-segment: %s ended at %.0fs; %s", seg.get("title") or seg["vid"], seg["end"],
                 "the next item" if more else "stopped")
        run(["next"] if more else ["stop"])
        return None
    if pos < seg.get("start", 0) - 30 and age > 20:
        # Played from elsewhere in the clip on purpose (a seek back): the
        # bound is for the song, so it goes.
        clear()
        return None
    if st.get("paused"):
        return _MAX_WAIT_S
    return max(_MIN_WAIT_S, min(_MAX_WAIT_S, left - _EARLY_S))
