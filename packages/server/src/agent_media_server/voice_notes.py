"""The words of a room's voice notes (server-contract.md §6.37).

From the list of what the bridged apps do (David, 20 Oct 2026, item 19):
"Voice notes transcribed: the words under the play button, and
searchable". Built once there was a speech-to-text (29 Oct 2026,
agent_media_core.transcribe).

A voice note in a room (a §6.29 `media` part of kind `voice`) is
transcribed in the background by whichever speech-to-text is set up: a new
one as it arrives, an older one the first time its page is read. Its words
go on the part as `transcript`, which the app shows under the play button
and the search index takes as the message's words (search.bodies). A
transcript is kept by event id, so a restart does not pay for it again;
one that failed is not tried again for a day.

Nothing happens with no speech-to-text set up, or with `MEDIA_VOICE_NOTES=0`.
"""

from __future__ import annotations

import logging
import os
import queue
import threading
import time

from ._jsonmap import JsonMap

log = logging.getLogger("agent-media.server.voice_notes")

STORE = JsonMap("voice-notes.json")
#: Transcripts kept, the newest.
KEEP = 5000
#: A failure is tried again after this long.
RETRY_S = 86400
#: Waiting at most, so a room full of old notes cannot run up a bill at once.
QUEUE_MAX = 200

_Q: queue.Queue = queue.Queue(maxsize=QUEUE_MAX)
_ASKED: set[str] = set()
_LOCK = threading.Lock()
_WORKER: list[threading.Thread | None] = [None]


def enabled() -> bool:
    if os.environ.get("MEDIA_VOICE_NOTES", "1") == "0":
        return False
    from agent_media_core import transcribe

    return transcribe.provider() is not None


def known(ev_id: str) -> str | None:
    """The words of voice note `ev_id`, if it has been transcribed."""
    row = STORE.get(ev_id)
    if isinstance(row, dict) and isinstance(row.get("text"), str):
        return row["text"]
    return None


def _failed_lately(ev_id: str) -> bool:
    row = STORE.get(ev_id)
    return isinstance(row, dict) and "failed_at" in row \
        and time.time() - float(row["failed_at"]) < RETRY_S


def _keep(ev_id: str, row: dict) -> None:
    def fn(rows: dict) -> bool:
        rows.pop(ev_id, None)
        rows[ev_id] = row
        while len(rows) > KEEP:
            rows.pop(next(iter(rows)))
        return True
    STORE.update(fn)


def want(room, ev_id: str) -> None:
    """Transcribe voice note `ev_id` of `room` in the background, once."""
    with _LOCK:
        if ev_id in _ASKED or known(ev_id) is not None or _failed_lately(ev_id):
            return
        if not enabled():
            return
        try:
            _Q.put_nowait((room, ev_id))
        except queue.Full:
            return
        _ASKED.add(ev_id)
        if _WORKER[0] is None or not _WORKER[0].is_alive():
            _WORKER[0] = threading.Thread(target=_work, name="voice-notes", daemon=True)
            _WORKER[0].start()


def _work() -> None:
    while True:
        try:
            room, ev_id = _Q.get(timeout=60)
        except queue.Empty:
            return
        try:
            transcribe_now(room, ev_id)
        except Exception:  # noqa: BLE001 — one note, not the worker
            log.exception("voice note %s", ev_id)
        finally:
            with _LOCK:
                _ASKED.discard(ev_id)


def transcribe_now(room, ev_id: str) -> str | None:
    """Fetch, transcribe and keep one voice note; put its words on the held
    message. The words, or None when it could not be done."""
    from agent_media_core import transcribe

    from . import room_media

    info = room.media.get(ev_id)
    if not info:
        return None
    path = room_media.fetch(info, thumb=False)
    if path is None:
        _keep(ev_id, {"failed_at": time.time(), "why": "not fetched"})
        return None
    try:
        words = transcribe.transcribe(path)
    except transcribe.SttError as e:
        log.info("voice note %s not transcribed: %s", ev_id, e)
        _keep(ev_id, {"failed_at": time.time(), "why": str(e)[:200]})
        return None
    _keep(ev_id, {"text": words, "at": time.time()})
    room.set_transcript(ev_id, words)
    return words
