"""Up next: held replies waiting for the reply being heard to finish
(server-contract.md §6.5 `up_next`, §11 `?after=1`).

David, 8 Nov 2026: "can we have keep reading pop-up when listening to speech
while opening a thread that will play ... So that it finishes what you're
listening to before the new one starts playing", and "Maybe we can have an Up
next section for the full media player beneath recently played if a whole
heap of unplayed threads have been opened with keep reading on".

A thread opened with `?after=1` whose held reply would play on the open
(thread_events._play_waiting) while another reply is being heard (speaking or
paused) does not cut in: the reply goes on the end of this queue. Once nothing
has been heard for IDLE_S, the first is played (`media replay --id ID
--resume`, which goes on through that thread's other unheard replies, as on
an open), and the next waits for that to end in turn. The queue is the
server's, so every device shows the same one (`/speech/now` `up_next`); a row
can be played now or taken off (`POST /speech/up-next`). A reply heard by
other means meanwhile (its ▶, a Play on the notice), or a question answered
meanwhile, is skipped when its turn comes. Kept in up-next.json, so a restart of the session host keeps it.
"""

from __future__ import annotations

import json
import logging
import sys
import threading
import time

log = logging.getLogger(__name__)

#: Nothing heard for this long before the next one plays: a replay's chained
#: replies have short gaps between them that are not its end.
IDLE_S = 2.5
#: Checked this often while the queue has anything in it.
POLL_S = 0.5
#: A started replay that has not been heard after this long has failed (or
#: played nothing): the next one goes.
START_GRACE_S = 45.0
#: At most this many waiting; an open past it plays at once, as before.
MAX_ITEMS = 50

_LOCK = threading.RLock()
_ITEMS: list[dict] | None = None  # loaded on first use
_DRAINER: threading.Thread | None = None


def _path():
    from agent_media_core._paths import state_dir

    return state_dir() / "up-next.json"


def _load() -> list[dict]:
    """Under `_LOCK`: the queue, read from disk the first time."""
    global _ITEMS
    if _ITEMS is None:
        try:
            raw = json.loads(_path().read_text())
            _ITEMS = [x for x in raw.get("items") or []
                      if isinstance(x, dict) and isinstance(x.get("id"), int) and x.get("session")]
        except (OSError, ValueError, AttributeError):
            _ITEMS = []
        if _ITEMS:
            _start()
    return _ITEMS


def _save() -> None:
    try:
        p = _path()
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps({"items": _ITEMS or []}))
        tmp.replace(p)
    except OSError as e:
        print(f"up-next: could not save: {e}", file=sys.stderr, flush=True)


def reply_live() -> bool:
    """Is a reply being heard now (speaking or paused)? A thinking line or a
    notification read out does not count, as for Now playing (stage.py)."""
    try:
        from . import speech

        s = speech.current_state()
    except Exception:  # noqa: BLE001 — unreadable is "nothing"
        return False
    return bool(s.get("speaking") or s.get("paused")) and not s.get("thinking") \
        and not s.get("notif")


def _busy() -> bool:
    """Is the voice taken: a reply heard, or one waiting for the voice?"""
    if reply_live():
        return True
    try:
        from . import speech

        return bool(speech.current_state().get("queued"))
    except Exception:  # noqa: BLE001
        return False


def add(session: str, rid: int, device: str = "") -> int | None:
    """Put `rid` (the held reply `session`'s open would have played) on the
    end. Its 1-based place, or None when the queue is full (play it now).
    A thread already waiting keeps its place (its reply is the newer row
    when it is a different one)."""
    with _LOCK:
        items = _load()
        for i, x in enumerate(items):
            if x["session"] == session:
                x["id"] = int(rid)
                _save()
                return i + 1
        if len(items) >= MAX_ITEMS:
            return None
        items.append({"session": session, "id": int(rid), "at": round(time.time(), 1),
                      **({"device": device} if device else {})})
        _save()
        place = len(items)
    print(f"up-next: {session[:8]} (reply {rid}) waits, #{place}", file=sys.stderr, flush=True)
    _start()
    return place


def remove(session: str) -> dict | None:
    """Take `session` off; its row, or None when it was not waiting. Its
    reply keeps its Play."""
    with _LOCK:
        items = _load()
        for i, x in enumerate(items):
            if x["session"] == session:
                del items[i]
                _save()
                return x
    return None


def play_now(session: str) -> dict | None:
    """Take `session` off and play its reply now, over what is heard (as an
    open did before). Its row, or None when it was not waiting."""
    x = remove(session)
    if x is not None:
        _play(x)
    return x


def listing() -> list[dict]:
    """`/speech/now` `up_next`: the queue in play order, each `{"session",
    "title", "id", "at"}`."""
    from . import sessions

    with _LOCK:
        items = [dict(x) for x in _load()]
    return [{"session": x["session"], "title": sessions.title_of(x["session"]) or "",
             "id": x["id"], "at": x.get("at")} for x in items]


def sessions_waiting() -> list[str]:
    with _LOCK:
        return [x["session"] for x in _load()]


def _heard(rid: int) -> bool:
    """Was the reply heard to its end meanwhile, in any copy?"""
    try:
        from agent_media_core import reply_position
        from agent_media_core.state import StateStore

        st = StateStore()
        row = st.history_row(int(rid)) or {}
        if not row:
            return True
        if (row.get("extras") or {}).get("heard"):
            return True
        if _answered(row):
            # A question answered while it waited has nothing left to ask
            # (David, 9 Nov 2026: "why is this ask question being read out
            # when I've already answered the question?"): heard, as a replay
            # answered part way is (cli replay-track `_answered_now`).
            st.mark_heard(int(rid))
            return True
        return reply_position.heard_to_any(row, store=st) is None
    except Exception:  # noqa: BLE001 — unsure: play it
        return False


def _answered(row: dict) -> bool:
    """Is `row` a question's read-out answered since it was said (the
    session's `ask` cut, set by the answer, at or after the row)?"""
    extras = row.get("extras") or {}
    session = str(extras.get("session") or extras.get("source_session") or "")
    if not (extras.get("ask") and session):
        return False
    from agent_media_core.intake.submit import _read_speech_cut

    at = _read_speech_cut(session).get("ask")
    began = row.get("started_at")
    return isinstance(at, (int, float)) and isinstance(began, (int, float)) and at >= began


def _play(x: dict) -> None:
    """Replay `x`'s reply from where it was left, detached (as
    thread_events._play_waiting does)."""
    import subprocess

    from agent_media_core._paths import state_dir
    from agent_media_core.procinfo import outliving_service

    rid = int(x["id"])
    print(f"up-next: playing {x['session'][:8]} (reply {rid})", file=sys.stderr, flush=True)
    try:
        from agent_media_core.state import StateStore

        StateStore().set_extra(rid, "opened_play", {
            "at": round(time.time(), 1), "up_next": True,
            **({"device": x["device"]} if x.get("device") else {})})
    except Exception:  # noqa: BLE001 — a note is never worth the replay
        log.exception("up-next: could not note the play on %s", rid)
    try:
        with open(state_dir() / "opened-replay.log", "ab") as err:
            subprocess.Popen(outliving_service([sys.executable, "-m", "agent_media_core.cli",
                                                "replay", "--id", str(rid), "--resume"]),
                             start_new_session=True, stdin=subprocess.DEVNULL,
                             stdout=subprocess.DEVNULL, stderr=err)
    except Exception:  # noqa: BLE001 — a missed replay leaves its Play
        log.exception("up-next: could not play %s", rid)


def _start() -> None:
    global _DRAINER
    with _LOCK:
        if _DRAINER is not None and _DRAINER.is_alive():
            return
        _DRAINER = threading.Thread(target=_drain, daemon=True, name="up-next")
        _DRAINER.start()


def _drain() -> None:
    """While anything waits: when the voice has been free IDLE_S, play the
    first, then wait for it to be heard (or START_GRACE_S) before looking
    again."""
    quiet_since: float | None = None
    started: float | None = None
    while True:
        with _LOCK:
            if not _load():
                return
        now = time.monotonic()
        busy = _busy()
        if started is not None:
            # The replay just started: wait for its voice, then for its end.
            if busy or now - started > START_GRACE_S:
                started = None
            else:
                time.sleep(POLL_S)
                continue
        if busy:
            quiet_since = None
        elif quiet_since is None:
            quiet_since = now
        elif now - quiet_since >= IDLE_S:
            with _LOCK:
                items = _load()
                x = items.pop(0) if items else None
                _save()
            if x is not None and not _heard(x["id"]):
                _play(x)
                started = time.monotonic()
            quiet_since = None
        time.sleep(POLL_S)


def _reset_for_tests() -> None:
    global _ITEMS
    with _LOCK:
        _ITEMS = None
