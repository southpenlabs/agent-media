"""`POST /session/stop` — the thing that is happening (server-contract.md §12).

Stop does one thing per press. With `speech: "auto"` (the default, what the
app's `onCancel` sends):

* the session is **working** → interrupt the turn, through the driver that owns
  it (driver/): the `interrupt` control request for a headless session,
  Escape for a pane (and only while it is working). Speech is left alone:
  what is playing or queued was said before the stop and is still true. What
  the interrupted turn says *after* the press is not: the `after` cutoff.
* not working, and **this thread's speech** is playing or paused → stop it:
  the clip (`SinkSpeech().stop`, what `media stop` does) and, through the
  `all` cut, the rest of that reply and everything this thread has queued.
* neither → nothing.

`speech: "silence"` (the app's second press within 5 s) also stops this
thread's speech whatever the turn is doing. `speech: "keep"` (a waiting
message's Send now, 2 Nov 2026) interrupts a working turn and nothing else:
no cutoff and no speech stopped, since the messages queued behind the turn
run next and their reply is to be heard. Another thread's speech is never
touched, and a session stopped on a question is never pressed — that answer
belongs to `/session/answer`.

**The per-session speech marker** is core's (`request_session_speech_cut` in
`intake/submit.py`): one marker per Claude session with two modes. `after`
skips this thread's replies submitted after the press, until the listener's
next turn; `all` skips every one of its replies submitted up to the press
that has not been heard. A skipped reply is still archived, marked flushed.
"""

from __future__ import annotations

import sys
import time

from . import auth, driver, sessions, speech

_MODES = ("auto", "silence", "keep")


def session_stop(session: str, mode: str, bearer: str) -> tuple[bool, dict]:
    session = (session or "").strip()
    if not sessions._SESSION.fullmatch(session):
        return False, {"error": "not a session id", "status": 400}
    user, err = auth.gate(bearer)
    if not user:
        return False, err
    mode = (mode or "auto").strip().lower()
    if mode not in _MODES:
        return False, {"error": "speech must be auto, silence or keep", "status": 400}
    drv = driver.for_session(session)
    before = drv.state(session)
    # The stop time. Both cuts are stamped with the press, not with whenever
    # the interrupt finished landing.
    pressed = time.time()

    # Whose speech is heard now. Read before interrupting: an interrupt ends
    # the turn, and a reply it had finished may start speaking a moment later
    # — that one is the cutoff's, not this press's.
    snap = speech.current_state()
    live_speech = bool(snap.get("speaking") or snap.get("paused"))
    this_thread = live_speech and str(snap.get("session") or "") == session

    interrupted, why, cutoff = False, None, None
    if before["state"] == "working":
        # The cutoff goes down before the keys, so a reply the turn finishes
        # while they land is already behind it — and comes up again if nothing
        # was interrupted, since that turn's reply is then still worth hearing.
        had_cutoff = True if mode == "keep" else speech.has_cutoff(session)
        cutoff = None if mode == "keep" else speech.cut_session(session, "after", pressed)
        ok, r = drv.interrupt(session)
        interrupted = bool(ok and r.get("interrupted"))
        if not interrupted:
            if not had_cutoff:
                speech.end_cutoff(session)
            cutoff = None
        if not ok:
            # 504: the keys went nowhere (a pane still working after Escape).
            return False, {**r, "session": session}
        why = r.get("why")
    elif not before["live"]:
        why = "not live"
    elif before["state"] == "approval":
        why = "waiting on a question"
    else:
        why = "not working"

    # This thread's speech, stopped: the clip it is on (only when it is the
    # one heard) and everything it has waiting. `silence` drops what is
    # waiting even while another thread is the one heard.
    stop_it = mode == "silence" or (mode == "auto" and this_thread
                                    and before["state"] != "working")
    flushed = 0
    if stop_it:
        flushed = len(speech.waiting_for(session, pressed))
        speech.cut_session(session, "all", pressed)
        if this_thread:
            err = speech.stop_speech()
            if err:
                print(f"stop: could not stop speech for {session[:8]} ({err})",
                      file=sys.stderr)
    silenced = stop_it and (this_thread or flushed > 0)
    if silenced:
        said = "stopped"
    elif this_thread:
        said = "left"
    elif live_speech:
        said = "other_thread"
    else:
        said = "idle"
    did = ("both" if interrupted and silenced else "interrupted" if interrupted
           else "silenced" if silenced else "nothing")
    after = drv.state(session)
    return True, {"session": session, "did": did, "interrupted": interrupted,
                  "why": None if interrupted else why, "speech": said,
                  "cutoff": cutoff, "flushed": flushed, "state": after["state"]}


def session_release(session: str, bearer: str) -> tuple[bool, dict]:
    """`POST /session/release` — Send now on a message held for a restart
    (server-contract.md §6.2.2 `held`; David, 3 Nov 2026: "a way of
    overriding the hold on a message for a restart"). The pending restart
    lets the session through: its held messages go now, it is held no more
    and not asked to end its turn, and the restart waits for it (sessiond.py
    `release`). Only a headless session is ever held."""
    session = (session or "").strip()
    if not sessions._SESSION.fullmatch(session):
        return False, {"error": "not a session id", "status": 400}
    user, err = auth.gate(bearer)
    if not user:
        return False, err
    from .driver import headless

    r = headless.call("release", session=session, timeout=30.0)
    if not r.get("ok"):
        code = str(r.get("code") or "internal")
        if code == "bad_request":
            # A session host from before the op: restarted, it has it.
            return False, {"error": "the session host is too old to send it now; "
                           "it goes after the restart", "code": "unsupported", "status": 501}
        return False, {"error": str(r.get("error") or "the session host refused"),
                       "code": code, "status": headless._STATUS.get(code, 500)}
    return True, {"session": session, "released": int(r.get("released") or 0),
                  "held": int(r.get("held") or 0), "through": bool(r.get("through"))}


def session_run_now(session: str, bearer: str) -> tuple[bool, dict]:
    """`POST /session/run-now` — Run now on a message held for a free
    session slot (server-contract.md §6.3 "Run now"; David, 11 Nov 2026).
    Its held messages are marked `slot: "high"` and go now, another
    session parked for them if every slot is busy (one only waiting on a
    card, a long command or background work first), which is resumed by
    itself when a slot frees (sessiond.py `run_now`). Not to be confused
    with `/session/priority`, the thread's speech level (§6.4)."""
    session = (session or "").strip()
    if not sessions._SESSION.fullmatch(session):
        return False, {"error": "not a session id", "status": 400}
    user, err = auth.gate(bearer)
    if not user:
        return False, err
    from .driver import headless

    r = headless.call("run_now", session=session, timeout=30.0)
    if not r.get("ok"):
        code = str(r.get("code") or "internal")
        if code == "bad_request":
            return False, {"error": "the session host is too old to run it now; it goes "
                           "when a session frees up", "code": "unsupported", "status": 501}
        return False, {"error": str(r.get("error") or "the session host refused"),
                       "code": code, "status": headless._STATUS.get(code, 500)}
    return True, {"session": session, "sent": int(r.get("sent") or 0),
                  "held": int(r.get("held") or 0), "bumped": r.get("bumped") or None,
                  "why": str(r.get("why") or "")}


def session_slots(bearer: str) -> tuple[bool, dict]:
    """`GET /sessions/slots` — who holds the host's session slots and why,
    and which threads wait for one (§6.3 "Run now"; sessiond.py `slots`),
    each with the thread's title."""
    user, err = auth.gate(bearer)
    if not user:
        return False, err
    from . import headless_mail
    from .driver import headless

    r = headless.call("slots", timeout=10.0)
    if not r.get("ok"):
        code = str(r.get("code") or "internal")
        if code == "bad_request":
            return False, {"error": "the session host is too old to say", "code": "unsupported",
                           "status": 501}
        return False, {"error": str(r.get("error") or "the session host refused"),
                       "code": code, "status": headless._STATUS.get(code, 500)}

    def titled(row: dict) -> dict:
        out = {k: v for k, v in row.items() if k != "first_text"}
        out["title"] = headless_mail._title(row) or None
        return out

    return True, {"live": int(r.get("live") or 0), "max": int(r.get("max") or 0),
                  "short": bool(r.get("short")), "free_mb": r.get("free_mb"),
                  "holders": [titled(x) for x in r.get("holders") or [] if isinstance(x, dict)],
                  "held": [titled(x) for x in r.get("held") or [] if isinstance(x, dict)],
                  "bumped": [titled(x) for x in r.get("bumped") or [] if isinstance(x, dict)]}
