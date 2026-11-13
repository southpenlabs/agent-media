"""Up next (server-contract.md §6.5 `up_next`, §11 `?after=1`; up_next.py).

A held reply a thread's open would play waits behind the reply being heard,
and plays once that one ends. Nothing here plays anything: `subprocess.Popen`
is replaced, and the speech state is a fake.
"""

from __future__ import annotations

import subprocess
import time

import pytest

from agent_media_server import speech, thread_events, up_next
from agent_media_visual import canvas

from test_contract import AUTH, call, server, shelf, signed_in, typed  # noqa: F401
from test_transcript import SID

SID_B = "11111111-2222-3333-4444-555555555555"


@pytest.fixture()
def voice(monkeypatch):
    """The speech snapshot, settable; replays recorded, never run."""
    state = {"kind": "state", "speaking": False}
    monkeypatch.setattr(speech, "current_state", lambda: dict(state))
    monkeypatch.setattr(canvas, "speech_state", lambda: dict(state))
    ran: list = []
    monkeypatch.setattr(subprocess, "Popen", lambda argv, **kw: ran.append(argv))
    # The listing's titles would ask tmux through subprocess.run, which the
    # fake Popen above breaks (a TypeError on a host with a cold cache).
    from agent_media_server import sessions
    monkeypatch.setattr(sessions, "title_of", lambda s: "")
    monkeypatch.setattr(up_next, "IDLE_S", 0.2)
    monkeypatch.setattr(up_next, "POLL_S", 0.02)
    monkeypatch.setattr(up_next, "START_GRACE_S", 0.3)
    up_next._reset_for_tests()
    yield state, ran
    with up_next._LOCK:
        up_next._ITEMS = []
    up_next._reset_for_tests()


def _row(text="waiting", session=SID) -> int:
    from agent_media_core.state import StateStore

    return StateStore().add_history(sink="speech", uri="/x.mp3", started_at=time.time(),
                                    target="sasonica", source="claude-code", text=text,
                                    extras={"held": True, "session": session})


def _wait(pred, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.02)
    return False


def _ids(ran):
    return [argv[argv.index("--id") + 1] for argv in ran]


def test_an_open_while_a_reply_is_heard_waits_then_plays(voice, monkeypatch):
    from agent_media_core.intake import toast

    state, ran = voice
    rid = _row()
    monkeypatch.setattr(toast, "take_for_opened", lambda s: rid if s == SID else None)
    state.update(speaking=True, session=SID_B)
    thread_events._play_waiting(SID, "p8a", after=True)
    assert ran == []
    assert [x["session"] for x in up_next.listing()] == [SID]
    assert up_next.listing()[0]["id"] == rid
    time.sleep(0.4)
    assert ran == []  # still speaking: it waits
    state.update(speaking=False)
    assert _wait(lambda: _ids(ran) == [str(rid)])
    assert up_next.listing() == []


def test_without_after_or_with_nothing_heard_it_plays_at_once(voice, monkeypatch):
    from agent_media_core.intake import toast

    state, ran = voice
    rid = _row()
    monkeypatch.setattr(toast, "take_for_opened", lambda s: rid if s == SID else None)
    state.update(speaking=True, session=SID_B)
    thread_events._play_waiting(SID, "p8a")  # no ?after=1: cuts in, as before
    assert _ids(ran) == [str(rid)]
    state.update(speaking=False)
    thread_events._play_waiting(SID, "p8a", after=True)  # nothing heard
    assert _ids(ran) == [str(rid), str(rid)]
    assert up_next.listing() == []


def test_several_play_in_order_each_after_the_last(voice):
    state, ran = voice
    a, b = _row("a"), _row("b", SID_B)
    state.update(speaking=True)
    assert up_next.add(SID, a) == 1
    assert up_next.add(SID_B, b) == 2
    assert up_next.add(SID, a) == 1  # already waiting: keeps its place
    state.update(speaking=False)
    assert _wait(lambda: _ids(ran) == [str(a)])
    # Its replay speaks: the next waits for it to end.
    state.update(speaking=True)
    time.sleep(0.4)
    assert _ids(ran) == [str(a)]
    state.update(speaking=False)
    assert _wait(lambda: _ids(ran) == [str(a), str(b)])


def test_a_reply_heard_meanwhile_is_skipped(voice):
    from agent_media_core.state import StateStore

    state, ran = voice
    rid = _row()
    state.update(speaking=True)
    up_next.add(SID, rid)
    StateStore().mark_heard(rid)
    state.update(speaking=False)
    assert _wait(lambda: up_next.listing() == [])
    time.sleep(0.2)
    assert ran == []


def test_a_question_answered_meanwhile_is_skipped(voice):
    """A held question waiting on Up next, answered on its card before its
    turn: it is not read out after the answer (9 Nov 2026)."""
    from agent_media_core.intake.submit import request_session_speech_cut
    from agent_media_core.state import StateStore

    state, ran = voice
    rid = StateStore().add_history(sink="speech", uri="/x.mp3", started_at=time.time() - 60,
                                   target="sasonica", source="claude-code", text="Which one?",
                                   extras={"held": True, "session": SID, "ask": True})
    other = _row(session=SID_B)
    state.update(speaking=True)
    up_next.add(SID, rid)
    up_next.add(SID_B, other)
    request_session_speech_cut(SID, "ask")
    state.update(speaking=False)
    assert _wait(lambda: len(ran) == 1)
    time.sleep(0.2)
    assert [a[a.index("--id") + 1] for a in ran] == [str(other)]
    assert (StateStore().history_row(rid)["extras"] or {}).get("heard")


def test_the_queue_outlives_a_restart(voice):
    state, _ran = voice
    state.update(speaking=True)
    rid = _row()
    up_next.add(SID, rid, "p8a")
    up_next._reset_for_tests()  # as a new process would
    assert [x["id"] for x in up_next.listing()] == [rid]


def test_the_endpoint_plays_now_or_takes_off(server, shelf, signed_in, voice):
    state, ran = voice
    state.update(speaking=True, session=SID_B)
    a, b = _row("a"), _row("b", SID_B)
    up_next.add(SID, a)
    up_next.add(SID_B, b)
    res, obj = call(server, "GET", "/speech/now", headers=AUTH)
    assert res.status == 200 and [x["session"] for x in obj["up_next"]] == [SID, SID_B]
    res, obj = call(server, "POST", "/speech/up-next", {"session": SID_B, "action": "remove"}, AUTH)
    assert res.status == 200 and [x["session"] for x in obj["up_next"]] == [SID]
    assert ran == []
    res, obj = call(server, "POST", "/speech/up-next", {"session": SID, "action": "play"}, AUTH)
    assert res.status == 200 and obj["up_next"] == [] and _ids(ran) == [str(a)]
    res, obj = call(server, "POST", "/speech/up-next", {"session": SID, "action": "play"}, AUTH)
    assert res.status == 404 and obj["error"] == "that thread is not up next"
    res, obj = call(server, "POST", "/speech/up-next", {"session": "x", "action": "play"}, AUTH)
    assert res.status == 400
    res, obj = call(server, "POST", "/speech/up-next", {"session": SID, "action": "skip"}, AUTH)
    assert res.status == 400


def test_the_stream_passes_after_on(server, shelf, signed_in, voice, monkeypatch):
    """`?after=1` on the stream reaches `_play_waiting`; without it, not."""

    calls: list = []
    monkeypatch.setattr(thread_events, "OPEN_DWELL_S", 0)
    monkeypatch.setattr(thread_events, "_play_waiting",
                        lambda s, d="", after=False: calls.append((s, after)))
    sub = thread_events.subscribe(SID, "p8a", after=True)
    assert sub is not None
    assert _wait(lambda: calls == [(SID, True)])
    thread_events.unsubscribe(*sub)
    sub = thread_events.subscribe(SID, "p8a")
    assert _wait(lambda: calls == [(SID, True), (SID, False)])
    thread_events.unsubscribe(*sub)


def test_mark_read_marks_the_threads_held_replies_heard(server, shelf, signed_in, voice):
    """`POST /speech/heard` (Mark read): the thread's held replies count as
    heard and it leaves up next; another thread's are untouched."""
    from agent_media_core.state import StateStore

    st = StateStore()
    a = _row("one")
    b = st.add_history(sink="speech", uri="/x.mp3", started_at=time.time(), target="sasonica",
                       source="claude-code", text="two",
                       extras={"held": True, "source_session": SID})
    other = _row("theirs", session=SID_B)
    plain = st.add_history(sink="speech", uri="/x.mp3", started_at=time.time(), target="sasonica",
                           source="claude-code", text="spoken", extras={"source_session": SID})
    up_next.add(SID, a)
    res, obj = call(server, "POST", "/speech/heard", {"session": SID}, AUTH)
    assert res.status == 200 and obj == {"ok": True, "marked": 2}
    assert st.history_row(a)["extras"]["heard"] is True
    assert st.history_row(b)["extras"]["heard"] is True
    assert "heard" not in st.history_row(other)["extras"]
    assert "heard" not in st.history_row(plain)["extras"]
    assert up_next.listing() == []
    res, obj = call(server, "POST", "/speech/heard", {"session": SID}, AUTH)
    assert res.status == 200 and obj["marked"] == 0
    res, obj = call(server, "POST", "/speech/heard", {"session": "x"}, AUTH)
    assert res.status == 400
    assert voice[1] == []


def test_mark_heard_from_the_player_ends_the_reply_and_marks_it(server, shelf, signed_in, voice, monkeypatch):
    """`POST /speech/heard {now: true}` (the player's Mark heard): the reply
    being read gets End of reply, and its row is heard with no resume point;
    with nothing being read, 404 (9 Nov 2026)."""
    from agent_media_core.state import StateStore

    st = StateStore()
    ran: list = []
    monkeypatch.setattr(speech, "_CTL", lambda action, arg, *a, **k: ran.append(action) or "")
    res, obj = call(server, "POST", "/speech/heard", {"now": True}, AUTH)
    assert res.status == 404 and ran == []
    rid = _row("being read")
    st.set_stopped_at(rid, 2)
    st.set_now_playing("speech", "/x.mp3", time.time(), target="sasonica",
                       extras={"replay": True, "history_id": rid, "source_session": SID,
                               "current_sentence_idx": 4})
    res, obj = call(server, "POST", "/speech/heard", {"now": True}, AUTH)
    assert res.status == 200 and obj == {"ok": True, "id": rid, "session": SID, "undo": True}
    assert ran == ["jump-end"]
    ex = st.history_row(rid)["extras"]
    assert ex["heard"] is True and "stopped_at" not in ex
    # Undo: unheard again, its resume point the sentence it was on; once only.
    res, obj = call(server, "POST", "/speech/heard", {"undo": True}, AUTH)
    assert res.status == 200 and obj == {"ok": True, "id": rid}
    ex = st.history_row(rid)["extras"]
    assert "heard" not in ex and ex["stopped_at"]["sentence"] == 4
    res, obj = call(server, "POST", "/speech/heard", {"undo": True}, AUTH)
    assert res.status == 404


def test_undoing_mark_heard_on_a_live_reply_finds_its_row(server, shelf, signed_in, voice, monkeypatch):
    """A live reply has no row while it is read: the undo finds the one filed
    under its start as it ends."""
    from agent_media_core.state import StateStore

    st = StateStore()
    monkeypatch.setattr(speech, "_CTL", lambda *a, **k: "")
    began = time.time()
    st.set_now_playing("speech", "/x.mp3", began, target="sasonica",
                       extras={"source_session": SID, "current_sentence_idx": 2})
    res, obj = call(server, "POST", "/speech/heard", {"now": True}, AUTH)
    assert res.status == 200 and obj["id"] is None
    rid = st.add_history(sink="speech", uri="/x.mp3", started_at=began, target="sasonica",
                         source="claude-code", text="live", extras={"source_session": SID, "heard": True})
    res, obj = call(server, "POST", "/speech/heard", {"undo": True}, AUTH)
    assert res.status == 200 and obj == {"ok": True, "id": rid}
    ex = st.history_row(rid)["extras"]
    assert "heard" not in ex and ex["stopped_at"]["sentence"] == 2


def test_a_held_play_marks_those_replies_heard_with_an_undo(server, shelf, signed_in, voice, monkeypatch):
    """`POST /speech/heard {ids}` (a Play key held, 14 Nov 2026): each is
    heard and loses its resume point, nothing is played, the thread waiting
    on one leaves up next; Undo gives each back what it had."""
    from agent_media_core.state import StateStore

    st = StateStore()
    ran: list = []
    monkeypatch.setattr(speech, "_CTL", lambda action, arg, *a, **k: ran.append(action) or "")
    held = _row("held")
    cut = st.add_history(sink="speech", uri="/x.mp3", started_at=time.time(), target="sasonica",
                         source="claude-code", text="spoken, stopped", extras={"source_session": SID})
    st.set_stopped_at(cut, 3)
    up_next.add(SID, held)
    res, obj = call(server, "POST", "/speech/heard", {"ids": [held, cut]}, AUTH)
    assert res.status == 200 and obj == {"ok": True, "marked": sorted([held, cut]), "undo": True}
    assert st.history_row(held)["extras"]["heard"] is True
    assert "stopped_at" not in st.history_row(cut)["extras"]
    assert up_next.listing() == [] and ran == []
    res, obj = call(server, "POST", "/speech/heard", {"undo": True}, AUTH)
    assert res.status == 200 and obj["ids"] == [held, cut]
    assert "heard" not in st.history_row(held)["extras"]
    assert st.history_row(cut)["extras"]["stopped_at"]["sentence"] == 3
    for bad in ([], ["1"], [True], "1", [0]):
        res, obj = call(server, "POST", "/speech/heard", {"ids": bad}, AUTH)
        assert res.status == 400 and obj["error"] == "ids must be history ids"
    res, obj = call(server, "POST", "/speech/heard", {"ids": [999999]}, AUTH)
    assert res.status == 404


def test_the_lists_play_held_marks_the_threads_unheard_with_an_undo(server, shelf, signed_in, voice):
    """`{session, with_undo: true}` (the thread list's Play held): the
    thread's unheard replies, as Mark read does, and Undo brings them back."""
    from agent_media_core.state import StateStore

    st = StateStore()
    a, b = _row("one"), _row("two")
    theirs = _row("theirs", session=SID_B)
    res, obj = call(server, "POST", "/speech/heard", {"session": SID, "with_undo": True}, AUTH)
    assert res.status == 200 and obj == {"ok": True, "marked": [a, b], "undo": True}
    assert "heard" not in st.history_row(theirs)["extras"]
    res, obj = call(server, "POST", "/speech/heard", {"undo": True}, AUTH)
    assert res.status == 200
    assert not st.history_row(a)["extras"].get("heard") and not st.history_row(b)["extras"].get("heard")
    res, obj = call(server, "POST", "/speech/heard", {"session": SID_B, "with_undo": True}, AUTH)
    res, obj = call(server, "POST", "/speech/heard", {"session": SID_B, "with_undo": True}, AUTH)
    assert res.status == 200 and obj == {"ok": True, "marked": [], "undo": False}
