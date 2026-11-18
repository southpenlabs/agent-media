"""A thread's question card waits while its unread replies are read.

David, 18 Oct 2026: "The card should wait too. otherwise I can't see the
follow along text". The voice of a question already waits (core
`_UNREAD_RANK`); the card, docked above the composer, took the room the
follow-along was in. While the speech row says an unread reply of this
thread is being read (`extras.unread`, written by `media replay`), the log
carries no `approval` and no unanswered ask part. Fakes only; the state dir
is the test's own.
"""

from __future__ import annotations

import time

import pytest

from agent_media_core.state import StateStore
from agent_media_server import threads

S = "6c73498c-02c1-4846-8350-a82006973571"
CARD = {"kind": "question", "key": "k", "questions": []}


@pytest.fixture(autouse=True)
def state(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    threads._unread_seen.clear()
    threads._coming_since.clear()
    yield StateStore()
    threads._unread_seen.clear()
    threads._coming_since.clear()


def _reading(st, session=S, **ex):
    st.set_now_playing("speech", uri="/c.mp3", started_at=time.time(), target="phone",
                       extras={"replay": True, "unread": True,
                               "source_session": session, **ex})


def test_the_card_waits_while_this_thread_is_read(state):
    _reading(state)
    assert threads.reading_unread(S)
    assert not threads.reading_unread("other")


def test_a_held_question_being_read_is_not_kept_back(state):
    _reading(state, ask=[{"question": "Which?"}])
    assert not threads.reading_unread(S)


def test_it_stays_back_across_the_hand_on_then_comes(state):
    _reading(state)
    assert threads.reading_unread(S, now=100.0)
    state.clear_now_playing("speech")
    assert threads.reading_unread(S, now=100.0 + threads.UNREAD_GRACE_S - 1)
    assert not threads.reading_unread(S, now=100.0 + threads.UNREAD_GRACE_S + 1)


def test_any_reading_of_this_thread_holds_it(state):
    state.set_now_playing("speech", uri="/c.mp3", started_at=time.time(), target="phone",
                          extras={"replay": True, "source_session": S})
    assert threads.reading_unread(S)


ASK = [{"question": "Which way?", "options": [{"label": "A"}, {"label": "B"}]}]
SENTS = ["Partly.", "With plain adb I can see the app.",
         "The trade-off is the stored data. Which way?",
         "Option 1: A.", "Option 2: B."]


def test_the_lead_prose_holds_it_and_the_question_brings_it(state):
    """19 Oct 2026: a live question turn read six sentences of prose with
    the card up over them. It comes up when the question is reached."""
    for idx, held in [(0, True), (1, True), (2, False), (3, False)]:
        state.set_now_playing("speech", uri="/c.mp3", started_at=time.time(),
                              target="phone",
                              extras={"source_session": S, "ask": ASK,
                                      "clip_sentences": SENTS,
                                      "current_sentence_idx": idx})
        assert threads.reading_unread(S) is held, idx


def test_paused_or_gone_does_not_hold_it(state):
    _reading(state, live_pause=True)
    assert not threads.reading_unread(S)
    threads._unread_seen.clear()
    _reading(state, writer_pid=2 ** 22 + 12345)
    assert not threads.reading_unread(S)


def _waiter(tmp_path, session=S, pid=None, pending=1):
    """A reply in the playback lock's waiter registry, as submit writes it:
    rank, seq, queue key, pending, speaker, kind."""
    import os
    d = tmp_path / "state" / "agent-media" / "speech-waiters"
    d.mkdir(parents=True, exist_ok=True)
    f = d / f"{pid or os.getpid()}.abc"
    f.write_text(f"2\n{time.time()!r}\nkey\n{pending}\n{session}\nreply")
    return f


def test_the_card_waits_for_its_read_out_to_start(state, tmp_path):
    """19 Nov 2026: "the card pops up then disappears for a while at the
    start". The question's voice is still rendering when the agent asks; the
    card waits for it rather than coming up and going again."""
    f = _waiter(tmp_path)
    assert threads.reading_unread(S, now=100.0)
    assert not threads.reading_unread("other", now=100.0)
    f.unlink()
    threads._unread_seen.clear()
    assert not threads.reading_unread(S, now=101.0)


def test_a_read_out_that_never_starts_holds_it_only_so_long(state, tmp_path):
    _waiter(tmp_path)
    assert threads.reading_unread(S, now=100.0)
    assert not threads.reading_unread(S, now=100.0 + threads.COMING_MAX_S + 1)


def test_a_dead_waiter_does_not_hold_it(state, tmp_path):
    _waiter(tmp_path, pid=2 ** 22 + 12345)
    assert not threads.reading_unread(S, now=100.0)


def _envelope(monkeypatch):
    msgs = [{"id": "a", "role": "assistant", "parts": [
                {"type": "text", "text": "One."},
                {"type": "ask", "ask": [], "status": "running", "answer": ""}]},
            {"id": "b", "role": "assistant", "parts": [
                {"type": "ask", "ask": [], "status": "running", "answer": ""}]},
            {"id": "c", "role": "assistant", "parts": [
                {"type": "ask", "ask": [], "status": "done", "answer": "Yes"}]}]
    monkeypatch.setattr(threads, "attach_pictures", lambda lines: None)
    monkeypatch.setattr(threads.sessions, "live_sessions", lambda: {S: "%7"})
    monkeypatch.setattr(threads.sessions, "_agent_of_pane", lambda pane: "claude")
    monkeypatch.setattr(threads.sessions, "approval_for", lambda *a, **k: dict(CARD))
    monkeypatch.setattr(threads.sessions, "suggestion_for", lambda *a, **k: "")
    monkeypatch.setattr(threads, "messages_for",
                        lambda *a, **k: ([dict(m, parts=list(m["parts"])) for m in msgs], False))
    monkeypatch.setattr(threads.recaps, "recap_for", lambda s: None)
    monkeypatch.setattr(threads.context, "context_for", lambda s: None)
    from agent_media_core import activity
    monkeypatch.setattr(activity, "attach", lambda s, lines: None)
    return threads._envelope(S, [])


def test_the_log_has_no_card_while_reading(state, monkeypatch):
    _reading(state)
    out = _envelope(monkeypatch)
    assert out["approval"] is None
    assert [m["id"] for m in out["messages"]] == ["a", "c"]
    assert [p["type"] for p in out["messages"][0]["parts"]] == ["text"]


def test_the_log_has_the_card_otherwise(state, monkeypatch):
    out = _envelope(monkeypatch)
    assert out["approval"] == CARD
    assert len(out["messages"]) == 3
