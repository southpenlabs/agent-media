"""The thread list's unheard counts, pushed the moment a reply is saved.

`/sessions/events?unheard=1` (session_events.py, contract §6.13) sends an
`unheard` frame — `{"unheard": {session: n}}`, speech.unheard_counts — on
connecting and whenever the counts change. The list's Play keys follow it
rather than the 2 s poll of /sessions/state (David, 18 Nov 2026: "why not at
the moment a reply is saved?").

A reply is saved by another process (the hook, intake) into the state
database, so the server cannot be told; it notices instead. One thread,
running only while a stream wants the frame, stats the database and its WAL
every `POLL_S` (microseconds); on a change it asks speech.unheard_counts,
which recounts only when the speech history's signature moved (9 ms to
check, 150 ms to count), and on new counts wakes every stream
(session_events.poke). A write from this process (a Mark heard) is noticed
the same way.
"""

from __future__ import annotations

import logging
import os
import threading

log = logging.getLogger(__name__)

#: How often the database's files are looked at while a stream wants counts.
POLL_S = 0.25

_LOCK = threading.Lock()
_WANTED = 0
_THREAD: threading.Thread | None = None
_VERSION = 0
_COUNTS: dict[str, int] | None = None


def _db_files() -> list[str]:
    from agent_media_core.state.store import default_db_path

    p = str(default_db_path())
    return [p, p + "-wal"]


def _stamp(files: list[str]) -> tuple:
    out = []
    for f in files:
        try:
            st = os.stat(f)
            out.append((st.st_mtime_ns, st.st_size))
        except OSError:
            out.append(None)
    return tuple(out)


def refresh() -> bool:
    """Count again (speech.unheard_counts, cheap when nothing moved); True
    and every stream woken when the counts changed."""
    global _VERSION, _COUNTS
    from . import speech

    try:
        counts = speech.unheard_counts()
    except Exception:  # noqa: BLE001 — the last counts stand
        log.exception("unheard watch: count failed")
        return False
    with _LOCK:
        if counts == _COUNTS:
            return False
        _COUNTS = dict(counts)
        _VERSION += 1
    from . import session_events

    session_events.poke()
    return True


def current() -> tuple[int, dict[str, int]]:
    """(version, counts): the version moves with every change of counts."""
    with _LOCK:
        have = _COUNTS is not None
    if not have:
        refresh()
    with _LOCK:
        return _VERSION, dict(_COUNTS or {})


def _run() -> None:
    global _THREAD
    files = _db_files()
    seen = _stamp(files)
    stop = threading.Event()
    while True:
        with _LOCK:
            if _WANTED <= 0:
                _THREAD = None
                return
        stop.wait(POLL_S)
        now = _stamp(files)
        if now != seen:
            seen = now
            refresh()


def want(on: bool) -> None:
    """A stream that wants `unheard` frames opened (True) or ended (False);
    the watching runs while any does."""
    global _WANTED, _THREAD
    with _LOCK:
        _WANTED = max(0, _WANTED + (1 if on else -1))
        if _WANTED and _THREAD is None:
            _THREAD = threading.Thread(target=_run, name="unheard-watch", daemon=True)
            _THREAD.start()
