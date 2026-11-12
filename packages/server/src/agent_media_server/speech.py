"""What is being said, for the app's speech bar.

Moved out of the canvas (reply.py's `speech_now`, canvas.py's `/speech/ctl`
whitelist). The speech snapshot itself is still made by the canvas — it is
the producer of the SSE `state` frame — so the canvas hands in two callbacks
at startup (`set_speech`): one that returns that snapshot, and one that runs
a whitelisted speech transport verb. This package never imports `visual`.
"""

from __future__ import annotations

import sys
import threading
import time
from typing import Callable

from . import audio, auth, mic_state, sessions, threads

#: What the app's speech player may do: the popup's listening keys — pause,
#: the sentence and paragraph steps, older/newer turn and replay, speed,
#: volume and a momentary mute — and "read from here": `goto-sentence` (jump
#: the reply being said to one of its sentences) and `replay-id` with a
#: `sentence` (play a recorded reply from one). The bearer is a listener's;
#: the popup's other keys (keep a pane muted, focus tmux, open URLs) are the
#: desk's. `again` is a headset's play with nothing loaded on the phone: the
#: last reply from where it was stopped, else the open thread's newest.
#: `stop` ends the reply being heard (`media stop`) and never touches the
#: turn, unlike POST /session/stop: full screen's speech rail (20 Oct 2026).
_APP_SPEECH_ACTIONS = frozenset({
    "toggle", "skip-", "skip+", "para-", "para+", "jump-end",
    "prev", "replay", "replay-unheard", "replay-id", "again", "speed-", "speed+", "speed0", "vol-", "vol+", "mute",
    "goto-sentence", "stop",
})

#: Speech verbs the server answers itself, never a `media` command (so not in
#: `_APP_SPEECH_ACTIONS`, every one of which the canvas turns into one):
#: `keep-reading` lifts a send's `read` cut (`keep_reading`).
_APP_SERVER_SPEECH_ACTIONS = frozenset({"keep-reading"})

#: The highest sentence index a request may name. Far past any reply (the
#: canvas keeps 120 lines of one); only here so a number is a number.
MAX_SENTENCE = 9999

#: The canvas's speech snapshot (`canvas.speech_state`), and its runner for one
#: whitelisted speech verb (`action, arg -> what media said`). Handed in by
#: the canvas at import (`set_speech`); a server with no canvas has a speech
#: bar that is always quiet and controls that do nothing.
_STATE: Callable[[], dict] | None = None
_CTL: Callable[..., str] | None = None


def set_speech(state: Callable[[], dict] | None = None,
               ctl: Callable[..., str] | None = None) -> None:
    """Hand in the canvas's speech snapshot and its transport runner."""
    global _STATE, _CTL
    _STATE, _CTL = state, ctl


def current_state() -> dict:
    """The live speech snapshot, as the canvas's SSE `state` frame has it."""
    return _STATE() if _STATE is not None else {"kind": "state", "speaking": False}


def run_ctl(action: str, arg: int, sentence: int | None = None,
            session: str = "", then: str = "") -> str:
    """Run one speech verb already checked against `_APP_SPEECH_ACTIONS`.
    `sentence` is the index for `goto-sentence` and `replay-id`'s "from
    here"; it rides as the canvas's `sarg` (its `arg` is clamped 1-999, and
    sentence zero is the one most often asked for). `session` is `replay`'s
    thread (and `replay-unheard`'s), already checked, and rides the same
    way. `then` is `replay-id`'s `then: "next"` thread (already checked):
    once the reply has played to its end, that thread's next reply follows
    (`media replay --then-next`)."""
    if _CTL is None:
        return ""
    if then:
        return _CTL(action, arg, "" if sentence is None else str(int(sentence)),
                    then=then)
    if session:
        return _CTL(action, arg, session)
    if sentence is None:
        return _CTL(action, arg)
    return _CTL(action, arg, str(int(sentence)))


def sentence_arg(value) -> int | None:
    """A request's sentence index: a whole number 0..MAX_SENTENCE, else None.
    `True` is not 1 and "3" is not 3 — the app sends numbers."""
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if 0 <= value <= MAX_SENTENCE else None


def goto_refusal(session: str = "") -> str:
    """Why `goto-sentence` cannot run now, or "". It moves the reply being
    said; with none, `media skip --to` would bring back whichever reply the
    canvas last showed and jump into that — a tap on one message must never
    start another. `session`, when the app names it, must be the one heard."""
    st = current_state()
    if not (st.get("speaking") or st.get("paused")):
        return "nothing is being said"
    if session and st.get("session") and st.get("session") != session:
        return "that reply is no longer being said"
    return ""


def row_sentences(row_id: int) -> list[str] | None:
    """The sentences a replay of history row `row_id` can start at
    (`cli.replay_sentence_map`), [] when it can only play from the top, or
    None when there is no such spoken row."""
    from agent_media_core.cli import replay_sentence_map
    from agent_media_core.state.store import StateStore

    row = StateStore().history_row(int(row_id))
    if not row or row.get("sink") != "speech":
        return None
    return replay_sentence_map(row)


def mark_now_heard() -> tuple[bool, dict]:
    """Mark heard from the player (David, 9 Nov 2026: "It would be nice to be
    able to mark it heard from the media player too"): the reply being read
    ends, as End of reply does, and counts as heard — no resume point, no ▶
    of an unheard one. A replay's row (`history_id`) is marked here, after
    the End (which keeps a resume point, `cli._keep_end_resume_point`); a
    live reply's row is written as it ends, heard because End ended it.
    False with a status when nothing is being read."""
    from agent_media_core.state import StateStore

    st = StateStore()
    np = st.get_now_playing("speech") or {}
    ex = np.get("extras") or {}
    if not np or not isinstance(ex, dict):
        return False, {"status": 404, "error": "nothing is being read"}
    rid = ex.get("history_id")
    rid = rid if isinstance(rid, int) else None
    at = ex.get("current_sentence_idx")
    run_ctl("jump-end", 1)
    if rid is not None:
        st.set_stopped_at(rid, None)
        st.mark_heard(rid)
    with _MARK_LOCK:
        _LAST_MARK[0] = {"id": rid, "started_at": np.get("started_at"),
                         "sentence": at if isinstance(at, int) and at >= 0 else 0,
                         "at": time.time()}
    return True, {"id": rid, "session": str(ex.get("source_session") or ""), "undo": True}


#: How long the player's Mark heard can be undone (the app's notice shows less).
MARK_UNDO_S = 120.0
#: The last Mark heard: its row (a live reply's is found by `started_at`, written
#: as it ends) and the sentence it was on.
_LAST_MARK: list = [None]
_MARK_LOCK = threading.Lock()


def undo_mark_heard() -> tuple[bool, dict]:
    """Undo the player's last Mark heard (David, 9 Nov 2026: "let's add the undo
    option"): the reply gets back the resume point End alone would have kept —
    the sentence it was on (`stopped_at`; the top, for the first) — so it is
    unheard again with its Resume. Once, and within MARK_UNDO_S."""
    from agent_media_core.state import StateStore

    with _MARK_LOCK:
        mark = _LAST_MARK[0]
        _LAST_MARK[0] = None
    if not mark or time.time() - mark["at"] > MARK_UNDO_S:
        return False, {"status": 404, "error": "nothing to undo"}
    st = StateStore()
    rid = mark["id"]
    end = time.monotonic() + 3.0
    while rid is None:
        # A live reply's row is written as it ends, a moment after the End.
        for r in st.recent_history(sink="speech", limit=40):
            try:
                same = abs(float(r.get("started_at")) - float(mark["started_at"])) < 0.01
            except (TypeError, ValueError):
                same = False
            if same and not (r.get("extras") or {}).get("listener"):
                rid = int(r["id"])
                break
        if rid is None:
            if time.monotonic() > end:
                return False, {"status": 409, "error": "that reply is not filed yet"}
            time.sleep(0.2)
    st.set_stopped_at(rid, int(mark["sentence"]))
    st.set_extra(rid, "heard", None)
    return True, {"id": rid}


def stop_speech() -> str:
    """Stop the clip playing now, on the player it is playing on — what
    `media stop` does. "" when done, else why not. `/session/stop` calls this
    only after checking the clip is the thread's own (stop.py)."""
    try:
        from agent_media_core.cli import _active_speech_target
        from agent_media_core.sinks.speech import SinkSpeech, mark_speech_stopped

        mark_speech_stopped()
        SinkSpeech().stop(_active_speech_target())
        return ""
    except Exception as e:  # noqa: BLE001 — reported, never raised into a route
        return str(e) or type(e).__name__


# --- a question read where it can no longer be answered -----------------------
#
# The black screen came down, or a thread left the screen: a question being
# read for a thread no longer in view is stopped, held for its thread and
# cued (core's question_gate.cut_unanswerable; David, 3 Nov 2026). Asked a
# moment later, once, not at once: a stream that reconnects, or a hop from
# the black screen to the thread under it, is not a look away.

#: Seconds after the change before the question is asked about.
QUESTION_RECHECK_S = 3.0
_QCHECK: list = [None]
_QCHECK_LOCK = threading.Lock()


def recheck_question_soon(delay: float = QUESTION_RECHECK_S) -> bool:
    """Ask, `delay` seconds from now, whether the question being read can
    still be answered, and cut it if not. Only in the running server (the
    watching table pinned at its start): a test must never stop a real
    player. True when scheduled."""
    from agent_media_core import watching

    if watching._PINNED is None:
        return False

    def run() -> None:
        from agent_media_core import question_gate

        question_gate.cut_unanswerable(stop_speech)

    with _QCHECK_LOCK:
        if _QCHECK[0] is not None:
            _QCHECK[0].cancel()
        t = threading.Timer(delay, run)
        t.daemon = True
        _QCHECK[0] = t
        t.start()
    return True


# --- the per-session speech marker (/session/stop, server-contract.md §12) -----
#
# Thin seams over core's marker (`intake/submit.py`), so stop.py reads as the
# table in §12 and the tests can record what was cut without a state dir.


def cut_session(session: str, mode: str, at: float) -> float | None:
    """Skip this thread's speech from `at`: `after` (what it says next, until
    the listener's next turn) or `all` (what it has not been heard saying)."""
    from agent_media_core.intake.submit import request_session_speech_cut

    return request_session_speech_cut(session, mode, at)


def reply_read(session: str, keep: bool = False) -> None:
    """The listener replied to this thread: stop reading its last reply at the
    end of the sentence, or with `keep`, leave it and tell the prompt hook to."""
    try:
        from agent_media_core.intake.submit import session_reply_read

        session_reply_read(session, keep=keep)
    except Exception as e:  # noqa: BLE001 — the reply goes in either way
        print(f"reply: could not mark the reply read ({e})", file=sys.stderr)


def keep_reading(session: str) -> bool:
    """Keep reading after a send: lift the `read` cut that send made, so the
    reply still speaking carries on (submit.lift_session_read_cut). True when
    there was one to lift."""
    from agent_media_core.intake.submit import lift_session_read_cut

    return lift_session_read_cut(session)


def has_cutoff(session: str) -> bool:
    """Whether an `after` cutoff already stands on this thread."""
    from agent_media_core.intake.submit import session_speech_cut

    return "after" in session_speech_cut(session)


def end_cutoff(session: str) -> None:
    """Lift this thread's `after` cutoff (the listener spoke, or nothing was
    interrupted after all)."""
    from agent_media_core.intake.submit import end_session_speech_cut

    end_session_speech_cut(session)


def waiting_for(session: str, at: float) -> list[dict]:
    """This thread's replies waiting for the voice, submitted by `at` — what an
    `all` cut at `at` drops (replies still rendering are not listed)."""
    from agent_media_core.intake.submit import speech_queue

    return [q for q in speech_queue()
            if q.get("session") == session and float(q.get("at") or 0) <= at]


# --- what is being said, for the app's mini player ------------------------------

#: `{session: (title, at)}` — a reply is polled every couple of seconds for as
#: long as it plays, and its title does not change while it does, so asking
#: once a minute is plenty.
_NOW_CACHE: dict[str, tuple[str, float]] = {}
_NOW_TTL_S = 60.0


def _session_title(session: str) -> str:
    # The one thread's name (the live sweep, else its manifest), not the
    # whole thread list: building that took 7–12 s, and the first live
    # answer waited on it, so the bar came up that long after the voice
    # (David, 29 Oct 2026: "still takes quite a while to come up").
    # A room (Matrix, a bridged Teams/WhatsApp chat) has no pane or shelf:
    # its name is the room's, as the chat list gives it — the group's, else
    # the people's — so away from the thread the mini player names it rather
    # than saying "Speaking" (David, 8 Nov 2026: "shouldn't it display the
    # group name or person's name?").
    title = sessions.title_of(session)
    if not title:
        from . import matrix
        try:
            r = matrix.room(session)
            title = r.title() if r is not None else ""
        except Exception:  # noqa: BLE001 — a name is a nicety; the bar still plays
            title = ""
    return title


#: `{session: (title, at)}` for the waiting replies: same TTL.
_TITLE_CACHE: dict[str, tuple[str, float]] = {}


def _queued_row(q: dict) -> dict:
    """One waiting reply, as `/speech/now` `queued` lists it."""
    session = str(q.get("session") or "")
    title = ""
    if sessions._SESSION.fullmatch(session):
        now = time.time()
        hit = _TITLE_CACHE.get(session)
        if not hit or now - hit[1] > _NOW_TTL_S:
            hit = (_session_title(session), now)
            _TITLE_CACHE[session] = hit
        title = hit[0]
    else:
        session = ""
    try:
        at = float(q.get("at") or 0) or None
    except (TypeError, ValueError):
        at = None
    return {"session": session or None, "title": title,
            "urgent": bool(q.get("urgent")), "at": at}


#: `(sessions, at)`: the threads whose reply was left paused, read from the
#: store at most this often — the app polls /speech/now every couple of
#: seconds, and the answer changes only when a reply ends or is replayed.
_LEFT_PAUSED: tuple[list[str], float] = ([], 0.0)
_LEFT_PAUSED_TTL_S = 5.0


def _left_paused() -> list[str]:
    """The sessions whose latest reply was left paused when the hold gave it
    up (StateStore.left_paused), for the thread list's paused mark."""
    global _LEFT_PAUSED
    now = time.time()
    if now - _LEFT_PAUSED[1] > _LEFT_PAUSED_TTL_S:
        try:
            from agent_media_core.state import StateStore
            got = sorted(StateStore().left_paused())
        except Exception as e:  # noqa: BLE001 — the bar matters more
            print(f"speech/now: left_paused: {e}", file=sys.stderr)
            got = _LEFT_PAUSED[0]
        _LEFT_PAUSED = (got, now)
    return list(_LEFT_PAUSED[0])


def speech_now(bearer: str, state: dict) -> tuple[bool, dict]:
    """`/speech/now`: what is being said right now, named for a person.

    The canvas's speech snapshot (`state`) says whether a voice is live and
    which session it belongs to; this adds what the app needs to show it
    anywhere — the conversation's title, so a tap can open it. Gated like
    `/conversation`: titles and sentences are the conversation's.
    """
    user, err = auth.gate(bearer)
    if not user:
        return False, err
    speaking = bool(state.get("speaking"))
    paused = bool(state.get("paused"))
    out = {"live": speaking or paused, "speaking": speaking, "paused": paused,
           "sentence": state.get("sentence") or "", "session": None,
           "title": "",
           "pos": state.get("pos"), "dur": state.get("dur"),
           "speed": state.get("speed"), "muted": bool(state.get("muted"))}
    # Where that voice is (or the next one will be): the bar shows it, and
    # the picker behind it is /audio/targets.
    out["target"] = state.get("target") or audio.speech_target_now(out["live"])
    # A recorded reply played again (the bar's replay, ▶ on a bubble): what is
    # heard is that reply, under its own session, and not a new one.
    out["replay"] = bool(out["live"] and state.get("replay"))
    out["thinking"] = bool(out["live"] and state.get("thinking"))
    # A notification or an alert rather than a reply or a question (§6.5):
    # hpo's ducker does not pause for it, nor for a thinking read-out.
    out["notif"] = bool(out["live"] and state.get("notif"))
    # A paired device's mic is open (§6.21e): hpo's ducker pauses its video
    # for this as for a speaking voice.
    out["mic"] = mic_state.any_open()
    # Which turn the player is on, keyed the way the log's lines are
    # (`{"at", "id"}`). The transcript can then match `pos` to a line it
    # already holds — which is the whole point of a line carrying its
    # timeline without being live: the bold survives losing the live row.
    if out["live"] and isinstance(state.get("turn"), dict):
        out["turn"] = state["turn"]
    # The pictures the canvas drew for the reply being heard, as a log
    # line's (§6.2 `images`, `figure`): the app's Now playing shows them
    # while the reply takes it over. Only once drawn; a figure can come a
    # few seconds after the voice starts.
    if out["live"] and state.get("key"):
        images, figure = threads.pictures_for_key(str(state["key"]))
        if images:
            out["images"] = images
            out["figure"] = bool(figure)
    # Replies said but not heard yet, because the voice is busy. The app's
    # "New reply waiting". Named, like the live one.
    out["queued"] = [_queued_row(q) for q in state.get("queued") or []
                     if isinstance(q, dict)]
    # Threads whose reply was paused and then let go by the hold (the
    # speech lock's cap): nothing is live, but the reply still waits on a
    # Resume, and the thread list keeps its paused mark.
    out["left_paused"] = [s for s in _left_paused()
                          if not (out["live"] and s == state.get("session"))]
    session = str(state.get("session") or "")
    if out["live"] and sessions._SESSION.fullmatch(session):
        now = time.time()
        hit = _NOW_CACHE.get(session)
        if not hit or now - hit[1] > _NOW_TTL_S:
            hit = (_session_title(session), now)
            _NOW_CACHE[session] = hit
        out.update(session=session, title=hit[0])
    return True, out


#: One sentence's audio for a browser that speaks replies itself
#: (`GET /speech/clip`): kept here so a replay is not rendered twice.
CLIP_MAX_CHARS = 2000
#: Kept this long since last written, then cleared on the next render.
CLIP_KEEP_S = 3 * 86400


def _prune_clips(folder) -> None:
    cutoff = time.time() - CLIP_KEEP_S
    for f in folder.iterdir():
        try:
            if f.stat().st_mtime < cutoff:
                f.unlink()
        except OSError:
            pass


def clip(bearer: str, text: str, voice: str = "") -> tuple[bool, dict]:
    """The app in a browser has no voice of its own (David, 16 Oct 2026:
    replies spoken in the browser, on the speaker chosen there). It holds
    the same `?speech=frames` stream the phone does, and each `load` op's
    `tts:` URI carries the words and the voice; this renders them to mp3
    here. A Microsoft voice (`edge:…`) is used as is; the phone's own
    voices are not this host's, so those get the server's (render/device.py
    server_voice's engine and voice)."""
    import hashlib
    import os
    from agent_media_core._paths import cache_dir
    from agent_media_core.render import render_text

    ok, detail = auth.may_control_speech(bearer)
    if not ok:
        return False, detail
    text = (text or "").strip()
    if not text:
        return False, {"status": 400, "error": "text, please"}
    if len(text) > CLIP_MAX_CHARS:
        return False, {"status": 413, "error": f"at most {CLIP_MAX_CHARS} characters"}
    if voice.startswith("edge:"):
        engine, name = "edge", voice[len("edge:"):]
    else:
        engine = (os.environ.get("MEDIA_RENDER_ENGINE")
                  or os.environ.get("CLAUDE_TTS_ENGINE") or "edge")
        name = (os.environ.get(f"MEDIA_RENDER_VOICE_{engine.upper().replace('-', '_')}")
                or os.environ.get("MEDIA_RENDER_VOICE") or "")
    key = hashlib.sha256(f"{engine}\0{name}\0{text}".encode()).hexdigest()[:32]
    folder = cache_dir() / "web-clips"
    folder.mkdir(parents=True, exist_ok=True)
    out = folder / f"{key}.mp3"
    if not (out.exists() and out.stat().st_size > 0):
        part = folder / f".{key}.{os.getpid()}.mp3"
        done, err = render_text(text, part, engine=engine, voice=name or None)
        if not done or not part.exists() or part.stat().st_size == 0:
            part.unlink(missing_ok=True)
            return False, {"status": 502, "error": f"could not render: {err or 'empty'}"}
        part.replace(out)
        _prune_clips(folder)
    return True, {"file": str(out), "size": out.stat().st_size, "type": "audio/mpeg"}


#: `GET /speech/recent`'s default and largest `limit`.
RECENT_DEFAULT = 20
RECENT_MAX = 50
#: A row's opening words, as the list shows them.
RECENT_TEXT = 140


def _recent_kind(row: dict, ex: dict) -> str:
    """What a speech-history row is to Recent replies: "reply" (an agent's
    reply), "question" (an agent asking), "message" (someone's words in a
    chat room), else "" — the listener's own turns, alerts and
    notifications, `media say` announcements and catch-up digests, which
    are not replies and have no thread to open."""
    if row.get("source") == "listener" or ex.get("listener"):
        return ""
    kind = ex.get("kind")
    if kind == "stop":
        return "reply"
    if kind == "notif":
        return "question" if isinstance(ex.get("ask"), list) else ""
    if row.get("source") == "matrix" and not kind:
        return "message"
    return ""


#: How long `unheard_counts` is reused: the list polls /sessions/state every
#: few seconds, from every open app.
_UNHEARD_TTL_S = 3.0
_UNHEARD_LOCK = threading.Lock()
_UNHEARD_CACHE: tuple[float | None, dict[str, int]] = (None, {})


def unheard_counts() -> dict[str, int]:
    """`/sessions/state` `unheard`: each thread's replies held and never
    played, by session id; threads with none are left out. The same test
    as `/speech/recent`'s `unheard` (a held copy of a reply heard in another
    copy is not counted; private threads are left out), so the thread
    list's Play key shows only where `replay-unheard` has something to read
    (David, 12 Nov 2026: "The play button on the threadlist should only
    display for unheard replies. not necessarily those marked unread")."""
    global _UNHEARD_CACHE
    with _UNHEARD_LOCK:
        at, counts = _UNHEARD_CACHE
        if at is not None and time.monotonic() - at <= _UNHEARD_TTL_S:
            return counts
        counts = _count_unheard()
        _UNHEARD_CACHE = (time.monotonic(), counts)
        return counts


def _count_unheard() -> dict[str, int]:
    from agent_media_core import reply_position
    from agent_media_core.private import hidden
    from agent_media_core.session_feed import _resume_of
    from agent_media_core.state import StateStore

    try:
        rows = StateStore().recent_history(sink="speech", limit=400)
    except Exception as e:  # noqa: BLE001 — none counted, not a 500
        print(f"unheard counts: {e}", file=sys.stderr)
        return {}
    counts: dict[str, int] = {}
    seen: set[tuple[str, str]] = set()
    secret: dict[str, bool] = {}
    for row in rows:    # newest first
        ex = row.get("extras") if isinstance(row.get("extras"), dict) else {}
        if not ex.get("held") or ex.get("heard") or not _recent_kind(row, ex):
            continue
        session = str(ex.get("source_session") or ex.get("session") or "")
        if not sessions._SESSION.fullmatch(session):
            continue
        key = str(ex.get("dedup_key") or "")
        if key:
            if (session, key) in seen:
                continue
            seen.add((session, key))
        if reply_position.heard_to_any(row, rows) != 0 or _resume_of(ex):
            continue
        if session not in secret:
            secret[session] = hidden(session)
        if not secret[session]:
            counts[session] = counts.get(session, 0) + 1
    return counts


def speech_recent(bearer: str, limit=None) -> tuple[bool, dict]:
    """`GET /speech/recent`: the replies read aloud lately, every thread's,
    newest first — the full player's Recent replies (David, 8 Nov 2026:
    "Can we get a list of the latest replies read from the full media
    player?"). One row per reply (a reply's held copy beside its spoken one
    is one row); private and sealed threads are left out, and so is what
    is not a reply (`_recent_kind`). Each row's heard state is the one its
    message carries in the log (§6.2 `spoken`): `unheard` for a reply held
    and never played, `resume` for one stopped part-way. Gated like
    `/speech/now`: titles and words are the conversations'."""
    user, err = auth.gate(bearer)
    if not user:
        return False, err
    try:
        n = int(limit) if limit not in (None, "") else RECENT_DEFAULT
    except (TypeError, ValueError):
        return False, {"status": 400, "error": "limit is a number"}
    n = max(1, min(RECENT_MAX, n))
    from agent_media_core import reply_position
    from agent_media_core.private import hidden
    from agent_media_core.session_feed import _resume_of
    from agent_media_core.state import StateStore

    from . import transcript

    try:
        rows = StateStore().recent_history(sink="speech", limit=max(400, n * 20))
    except Exception as e:  # noqa: BLE001 — an empty list, not a 500
        print(f"speech/recent: {e}", file=sys.stderr)
        rows = []
    items: list[dict] = []
    seen: set[tuple[str, str]] = set()
    titles: dict[str, str] = {}
    secret: dict[str, bool] = {}
    for row in rows:    # newest first
        ex = row.get("extras") if isinstance(row.get("extras"), dict) else {}
        kind = _recent_kind(row, ex)
        if not kind:
            continue
        session = str(ex.get("source_session") or ex.get("session") or "")
        if not sessions._SESSION.fullmatch(session):
            continue
        if session not in secret:
            secret[session] = hidden(session)
        if secret[session]:
            continue
        key = str(ex.get("dedup_key") or "")
        if key:
            if (session, key) in seen:
                continue
            seen.add((session, key))
        text = transcript.reply_preview(str(row.get("text") or ""))
        if not text:
            continue
        if session not in titles:
            try:
                titles[session] = _session_title(session)
            except Exception:  # noqa: BLE001 — a name, never a reason to fail
                titles[session] = ""
        title = titles[session] or str(ex.get("title") or ex.get("source_title") or "")
        heard_to = reply_position.heard_to_any(row, rows)
        resume = _resume_of(ex)
        durs = ex.get("clip_durations_s") or []
        try:
            dur = round(sum(float(d or 0) for d in durs), 1) or None
        except (TypeError, ValueError):
            dur = None
        item = {"id": int(row.get("id") or 0), "session": session, "title": title,
                "kind": kind, "text": text[:RECENT_TEXT],
                "at": round(float(row.get("started_at") or 0), 3),
                "dur_s": dur, "heard": heard_to is None}
        if heard_to == 0 and not resume:
            item["unheard"] = True
        if resume and heard_to is not None:
            item["resume"] = resume
        items.append(item)
        if len(items) >= n:
            break
    return True, {"items": items}
