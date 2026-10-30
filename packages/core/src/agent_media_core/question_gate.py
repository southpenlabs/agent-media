"""Read a question card aloud only where it can be answered (David, 30 Oct 2026).

"I don't think a question should really be read without the question card
in view unless in black screen mode because you're unable to answer it …
unless it's in hands-free mode of course."

A question (an AskUserQuestion read-out, `metadata["ask"]`) is answerable,
and so read in full, when (`answerable`):

- its thread is on screen in the app (watching.py's open streams; a pocket
  lease is the screen off, so it does not count), or its pane is the one the
  desk is on, or it runs in another app's front-end (toast.py);
- black screen mode is up on a device (watching.py's black lease, `POST
  /device/black`): the listener answers by voice there;
- the thread is hands-free (voice_mode.py: the app's loop is on).

Otherwise the question is held, with its Play and toast, like a reply
nobody was looking at (intake/toast.py `remember`): opening its thread plays
it, whatever the thread's level. In its place a short cue is spoken (`cue`):
"A question in <thread>." — not for a Quiet thread, which speaks nothing,
and at most once a minute per thread (`CUE_AGAIN_S`), once per card (a card
of several parts gets one cue). The cue is not a question (no `ask`), so it
is never itself held by this rule, and carries `ask_cue`.

Asked again when the question gets its turn to speak (`left_by_now`): one
that was answerable when it came but waited behind other speech while the
listener left its thread is held and cued then.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time

from . import _lock as fcntl
from ._paths import state_dir
from .types import Event, Priority

log = logging.getLogger(__name__)

#: A second cue for the same thread within this many seconds is not spoken.
CUE_AGAIN_S = 60.0
CUE_NAME = "question-cue.json"
#: How many words of the thread's title the cue says.
TITLE_WORDS = 6
#: How old the "answerable" verdict may be before it is asked again at the
#: moment the question gets its turn to speak (as submit's _REWATCH_AFTER_S).
RECHECK_AFTER_S = 5.0


def answerable(session: str) -> bool:
    """Can `session`'s question be answered where it would be read? Never
    raises: a failed check reads it, as before."""
    try:
        from . import voice_mode, watching
        from .intake import toast

        return bool(watching.black_screen_on()
                    or (session and voice_mode.active(session))
                    or watching.is_open(session)      # on screen; not a pocket lease
                    or toast._in_other_app()
                    or toast.desk_shows_pane())
    except Exception as e:  # noqa: BLE001
        log.info("question gate: check failed, reading it: %s", e)
        return True


def cue_text(metadata: dict | None) -> str:
    """"A question in <thread>." from the event's place (title, else window,
    else tmux session), a few words of it; "A question is waiting." without."""
    md = metadata or {}
    title = ""
    for k in ("title", "source_title", "window", "tmux"):
        v = md.get(k)
        if isinstance(v, str) and v.strip():
            title = v
            break
    words = title.split()
    if not words:
        return "A question is waiting."
    short = " ".join(words[:TITLE_WORDS]).rstrip(".,;:!?-–— ")
    return f"A question in {short}." if short else "A question is waiting."


def _claim_cue(session: str, card: str = "", now: float | None = None) -> bool:
    """Is a cue for `session` (this card, `card`) due? Records it when so."""
    now = time.time() if now is None else now
    path = state_dir() / CUE_NAME
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path.with_suffix(".lock"), "a") as lk:
            fcntl.flock(lk.fileno(), fcntl.LOCK_EX)
            try:
                try:
                    rows = json.loads(path.read_text())
                except (OSError, ValueError):
                    rows = {}
                if not isinstance(rows, dict):
                    rows = {}
                rows = {s: r for s, r in rows.items() if isinstance(r, dict)
                        and now - float(r.get("at") or 0) < max(CUE_AGAIN_S, 3600.0)}
                last = rows.get(session) or {}
                if now - float(last.get("at") or 0) < CUE_AGAIN_S:
                    return False
                if card and last.get("card") == card:
                    return False
                rows[session] = {"at": now, "card": card}
                tmp = path.with_suffix(f".{os.getpid()}.tmp")
                tmp.write_text(json.dumps(rows))
                tmp.replace(path)
            finally:
                fcntl.flock(lk.fileno(), fcntl.LOCK_UN)
    except OSError:
        return True
    return True


def cue_event(question: Event, session: str) -> Event | None:
    """The cue to speak for `question`, held because it could not be
    answered, or None: a Quiet thread, or one cued a moment ago."""
    from .speak_priority import level_of

    level = level_of(session)
    if level == "quiet":
        return None
    md = question.metadata or {}
    card = str(md.get("tool_use_id") or "")
    if not _claim_cue(session, card):
        return None
    keep = {k: md[k] for k in ("pane", "tmux", "window", "title", "voice_from")
            if k in md}
    return Event(text=cue_text(md), source=question.source,
                 priority=Priority.HIGH if level == "interrupt" else Priority.NORMAL,
                 voice=question.voice,
                 metadata={**keep, "kind": "notif", "ask_cue": True, "session": session})


def hold(question: Event, session: str) -> Event | None:
    """Hold `question` for its thread (toast, `held`, the card's later parts
    held too) and answer the cue to speak in its place, or None."""
    from .intake import toast

    md = question.metadata
    md.pop("ask_answerable_at", None)
    toast.remember(question, ask=True)
    card = str(md.get("tool_use_id") or "")
    if card:
        try:
            from . import pending_asks

            pending_asks.update(session, card, held=True)
        except Exception:  # noqa: BLE001
            pass
    if md.get("ask_part"):
        return None          # one cue per card: its first part had it
    return cue_event(question, session)


def speak_cue(cue: Event | None, *, wait: bool = True, submit=None) -> None:
    """Submit the cue (through `submit`, the caller's submit_event, else
    intake.submit's); with `wait` false, from a thread (not a daemon, so the
    hook's process lives until it has been said)."""
    if cue is None:
        return
    if submit is None:
        from .intake.submit import submit_event as submit

    def run():
        try:
            submit(cue)
        except Exception as e:  # noqa: BLE001
            log.info("question gate: cue failed: %s", e)

    if wait:
        run()
    else:
        threading.Thread(target=run, name="question-cue").start()


def left_by_now(question: Event, session: str) -> bool:
    """A question that was answerable when it came (`ask_answerable_at`), whose
    turn to speak came after the listener left it. True after holding it and
    starting its cue, so the caller archives it unplayed. Never raises."""
    md = question.metadata
    if not isinstance(md, dict) or md.get("held") or not md.get("ask"):
        return False
    try:
        at = float(md.get("ask_answerable_at") or 0)
    except (TypeError, ValueError):
        return False
    if not at or time.time() - at < RECHECK_AFTER_S or answerable(session):
        return False
    try:
        cue = hold(question, session)
    except Exception as e:  # noqa: BLE001 — read it rather than lose it
        log.info("question gate: hold failed, reading it: %s", e)
        md.pop("held", None)
        return False
    log.info("question gate: %s left before its question's turn; held", session[:8])
    speak_cue(cue, wait=False)
    return True
