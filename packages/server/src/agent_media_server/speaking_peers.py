"""Telling other servers when this one reads a reply out, so their screens
pause for it (agent-mail #1440, 28 Nov 2026).

hpo's desktop pause helper is paired with red2 only, and a reply in a thread
red5 hosts is read through red5's own server: red2's `GET /screen/now` never
saw it speaking, so hpo played on through it. Each server a speaking link
names (`media-visual-canvas pair --device NAME --speaking` there, then
`media peer pair <alias> '<link>'` here; peer_link.speaking_links) is sent
this server's speech state at `POST /screen/elsewhere` (screens.elsewhere
there): when it changes, and each REPEAT_S while speaking, since the far
side forgets a state after a few seconds so a lost "stopped" never holds a
screen paused.

Nothing runs a snapshot (a `media popup-status` spawn) while the frames hub
knows its player is idle, but one every IDLE_CHECK_S in case it is wrong.
With no speaking link the loop only looks for one now and then.
"""

from __future__ import annotations

import threading
import time
from typing import Callable

from agent_media_core import failures as _failures

#: How often the loop looks, while it has somewhere to tell.
TICK_S = 0.5
#: While speaking, the state is sent again this often.
REPEAT_S = 1.0
#: A snapshot at least this often while the frames hub says idle.
IDLE_CHECK_S = 5.0
#: Without a frames hub to ask, a snapshot this often.
BLIND_CHECK_S = 1.0
#: How long the list of speaking links is kept before it is read again.
LINKS_TTL_S = 30.0
SEND_TIMEOUT_S = 2.0

_STARTED = False


def _links() -> list[dict]:
    from agent_media_core import peer_link

    return peer_link.speaking_links()


def _send(link: dict, body: dict) -> None:
    from agent_media_core import peer_link

    status, got = peer_link._http(link["url"].rstrip("/") + "/screen/elsewhere", body,
                                  token=link["token"], timeout=SEND_TIMEOUT_S)
    if status != 200:
        raise peer_link.PeerError(f"{link['url']}/screen/elsewhere said {status}: "
                                  f"{got.get('error') or got}")


def _snapshot() -> dict:
    from . import speech

    return speech.current_state()


def _busy() -> bool | None:
    from . import speech_frames

    return speech_frames.busy("speech")


def _reading() -> bool:
    from agent_media_core.intake import submit

    return submit.reply_reading()


def _player() -> dict | None:
    from . import devices, speech_frames

    dev = speech_frames.playing_device("speech")
    return devices.player_ref(dev) if dev else None


def state_of(snap: dict, player: dict | None, reading: bool = False) -> dict:
    """The body sent: {speaking, thinking, dur, player, reading}. `reading`:
    a reply holds the playback token here, gaps between its sentences
    included (submit.reply_reading), so a screen paused for it stays paused
    through them."""
    speaking = bool(snap.get("speaking"))
    dur = snap.get("dur")
    try:
        dur = round(float(dur), 1) if dur is not None else None
    except (TypeError, ValueError):
        dur = None
    return {"speaking": speaking, "thinking": bool(snap.get("thinking")) and speaking,
            "dur": dur if speaking else None, "player": player if speaking else None,
            "reading": bool(reading)}


class Teller:
    """One loop's memory: what was last sent and when. `step` is one tick,
    for tests to drive with their own clock and fakes."""

    def __init__(self, links: Callable[[], list[dict]] = _links,
                 send: Callable[[dict, dict], None] = _send,
                 snapshot: Callable[[], dict] = _snapshot,
                 busy: Callable[[], bool | None] = _busy,
                 player: Callable[[], dict | None] = _player,
                 reading: Callable[[], bool] = _reading,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.links, self.send, self.snapshot = links, send, snapshot
        self.busy, self.player, self.clock = busy, player, clock
        self.reading = reading
        self.last: dict | None = None
        self.sent_at: float | None = None
        self.snap_at: float | None = None
        self._links: list[dict] = []
        self._links_at: float | None = None

    def _current_links(self, t: float) -> list[dict]:
        if self._links_at is None or t - self._links_at >= LINKS_TTL_S:
            try:
                self._links = self.links()
            except Exception as e:  # noqa: BLE001 - no links is no telling
                _failures.swallowed(e)
                self._links = []
            self._links_at = t
        return self._links

    def step(self) -> bool:
        """One tick; whether anything was sent."""
        t = self.clock()
        links = self._current_links(t)
        if not links:
            return False
        speaking = bool(self.last and (self.last["speaking"] or self.last.get("reading")))
        b = self.busy()
        every = IDLE_CHECK_S if b is False else BLIND_CHECK_S
        if not (b or speaking or self.snap_at is None or t - self.snap_at >= every):
            return False
        try:
            snap = self.snapshot()
        except Exception as e:  # noqa: BLE001 - a missed tick is the next one's
            _failures.swallowed(e)
            return False
        self.snap_at = t
        try:
            reading = bool(self.reading())
        except Exception as e:  # noqa: BLE001 - unknown is the voice alone
            _failures.swallowed(e)
            reading = False
        cur = state_of(snap, None, reading)
        if cur["speaking"]:
            try:
                cur["player"] = self.player()
            except Exception as e:  # noqa: BLE001 - unplaced is still a voice
                _failures.swallowed(e)
        changed = cur != self.last
        due = (cur["speaking"] or cur["reading"]) and (self.sent_at is None or t - self.sent_at >= REPEAT_S)
        if not (changed or due):
            return False
        for link in links:
            try:
                self.send(link, cur)
            except Exception as e:  # noqa: BLE001 - another server down is its own
                _failures.swallowed(e)
        self.last, self.sent_at = cur, t
        return True


def start() -> None:
    """The loop, on a daemon thread, once per process."""
    global _STARTED
    if _STARTED:
        return
    _STARTED = True
    teller = Teller()

    def loop() -> None:
        while True:
            try:
                teller.step()
            except Exception as e:  # noqa: BLE001 - the loop outlives a bad tick
                _failures.swallowed(e)
            time.sleep(TICK_S)

    threading.Thread(target=loop, name="speaking-peers", daemon=True).start()
