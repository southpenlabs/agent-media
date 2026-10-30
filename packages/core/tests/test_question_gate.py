"""A question is read in full only where it can be answered (question_gate.py;
David, 30 Oct 2026): its thread on screen, black screen mode, hands-free.
Otherwise it is held and a short cue is said instead."""

from __future__ import annotations

import hashlib
import json
import os
import time

import pytest

from agent_media_core import question_gate, speak_priority, voice_mode, watching
from agent_media_core.intake import toast
from agent_media_core.types import Event, Priority, Source

SID = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    """Nothing here may reach a real player: a throwaway home, so the env
    file is never read, over conftest's scrubbed routes; no pane, no app."""
    for k in list(os.environ):
        if k.startswith(("TMUX", "PASEO_")):
            monkeypatch.delenv(k, raising=False)
    for d in ("HOME", "XDG_STATE_HOME", "XDG_CACHE_HOME", "XDG_CONFIG_HOME"):
        monkeypatch.setenv(d, str(tmp_path / d.lower()))
    monkeypatch.setenv("MEDIA_EARCONS", "0")
    monkeypatch.setattr(toast, "show", lambda where: None)
    monkeypatch.setattr(toast, "_tmux", lambda args: "")
    watching.publish({})


def _question(**md) -> Event:
    return Event(text="Ship it? Yes, or no.", source=Source.CLAUDE_CODE,
                 priority=Priority.HIGH, voice="af_heart",
                 metadata={"kind": "notif", "ask": True, "session": SID,
                           "title": "Wire the question gate", **md})


def test_nobody_looking_is_not_answerable():
    assert not question_gate.answerable(SID)


def test_on_screen_is_answerable():
    watching.publish({SID: 1})
    assert question_gate.answerable(SID)
    assert not question_gate.answerable("other")
    watching.publish({})
    assert not question_gate.answerable(SID)


def test_a_pocket_lease_is_the_screen_off():
    speak_priority.set_level(SID, "pocket")
    watching.pocket(SID, True)
    assert watching.is_open(SID, pocketed=True)
    assert not question_gate.answerable(SID)


def test_black_screen_mode_is_answerable_until_its_lease_ends(monkeypatch):
    assert watching.black("p8a", True) > time.time()
    assert question_gate.answerable(SID) and question_gate.answerable("other")
    assert watching.black("p8a", False) == 0
    assert not question_gate.answerable(SID)
    watching.black("p8a", True)
    (watching.state_dir() / watching.BLACK_NAME).write_text(json.dumps({"p8a": 1.0}))
    assert not question_gate.answerable(SID)            # ran out: app killed
    monkeypatch.setenv("MEDIA_BLACK_S", "0")
    assert watching.black("p8a", True) == 0             # leases off
    assert not watching.black_screen_on()


def test_hands_free_is_answerable():
    voice_mode.mark(SID, True)
    assert question_gate.answerable(SID)
    assert not question_gate.answerable("other")
    voice_mode.mark(SID, False)
    assert not question_gate.answerable(SID)


def test_the_cue_names_the_thread_in_a_few_words():
    assert question_gate.cue_text({"title": "Fix the bed."}) == "A question in Fix the bed."
    assert (question_gate.cue_text({"title": "one two three four five six seven eight"})
            == "A question in one two three four five six.")
    assert question_gate.cue_text({"window": "agent-media"}) == "A question in agent-media."
    assert question_gate.cue_text({}) == "A question is waiting."


def test_the_cue_is_short_not_a_question_and_not_repeated(monkeypatch):
    cue = question_gate.cue_event(_question(tool_use_id="t1"), SID)
    assert cue.text == "A question in Wire the question gate."
    assert cue.metadata["ask_cue"] and "ask" not in cue.metadata
    assert cue.metadata["session"] == SID and cue.priority == Priority.NORMAL
    assert question_gate.cue_event(_question(tool_use_id="t2"), SID) is None   # within 60 s
    assert question_gate.cue_event(_question(tool_use_id="t2"), "other") is not None
    monkeypatch.setattr(question_gate, "CUE_AGAIN_S", 0.0)
    assert question_gate.cue_event(_question(tool_use_id="t1"), SID) is None   # that card had one
    assert question_gate.cue_event(_question(tool_use_id="t3"), SID) is not None


def test_a_quiet_thread_gets_no_cue_and_interrupt_a_high_one(monkeypatch):
    monkeypatch.setattr(question_gate, "CUE_AGAIN_S", 0.0)
    speak_priority.set_level(SID, "quiet")
    assert question_gate.cue_event(_question(tool_use_id="q"), SID) is None
    speak_priority.set_level(SID, "interrupt")
    assert question_gate.cue_event(_question(tool_use_id="i"), SID).priority == Priority.HIGH
    speak_priority.set_level(SID, "auto")
    assert question_gate.cue_event(_question(tool_use_id="a"), SID).priority == Priority.NORMAL


def test_hold_puts_up_the_toast_and_a_later_part_gets_no_cue():
    q = _question(tool_use_id="t1")
    cue = question_gate.hold(q, SID)
    assert q.metadata["held"] and cue is not None
    assert [r for r in toast.listing()]
    part = _question(tool_use_id="t9", ask_part=1)
    assert question_gate.hold(part, SID) is None and part.metadata["held"]


def _row(store, q):
    return store.add_history(sink="speech", uri="/x.mp3", started_at=time.time(),
                             target="phone", source="claude-code", text=q.text,
                             extras={"held": True, "session": SID, "ask": True,
                                     "dedup_key": q.metadata["dedup_key"]})


def test_an_auto_threads_held_question_plays_when_the_thread_opens(monkeypatch):
    from agent_media_core.state import StateStore

    monkeypatch.setenv("MEDIA_TOAST_RENDER_WAIT_S", "0")
    st = StateStore()
    for level in ("auto", "interrupt"):
        speak_priority.set_level(SID, level)
        q = _question(tool_use_id=level)
        question_gate.hold(q, SID)
        rid = _row(st, q)
        assert toast.take_for_opened(SID) == rid
        assert toast.take_for_opened(SID) is None
    # Not anything else of an auto thread's: only a question is held for it.
    reply = Event(text="done", source=Source.CLAUDE_CODE, priority=Priority.NORMAL,
                  metadata={"kind": "stop", "session": SID})
    toast.remember(reply)
    st.add_history(sink="speech", uri="/x.mp3", started_at=time.time(), target="phone",
                   source="claude-code", text="done",
                   extras={"held": True, "session": SID,
                           "dedup_key": hashlib.sha1(b"done").hexdigest()})
    assert toast.take_for_opened(SID) is None
    speak_priority.set_level(SID, "quiet")
    q = _question(tool_use_id="quiet")
    question_gate.hold(q, SID)
    _row(st, q)
    assert toast.take_for_opened(SID) is None


def test_left_before_its_turn_is_held_and_cued(monkeypatch):
    from agent_media_core.intake import submit

    sent = []
    monkeypatch.setattr(submit, "submit_event", lambda e, **k: sent.append(e))
    q = _question(tool_use_id="t1", ask_answerable_at=time.time())
    assert not question_gate.left_by_now(q, SID)        # just decided
    q.metadata["ask_answerable_at"] = time.time() - 30
    watching.publish({SID: 1})
    assert not question_gate.left_by_now(q, SID)        # still on screen
    watching.publish({})
    assert question_gate.left_by_now(q, SID)
    assert q.metadata["held"] and "ask_answerable_at" not in q.metadata
    deadline = time.time() + 5
    while not sent and time.time() < deadline:
        time.sleep(0.01)
    assert [e.text for e in sent] == ["A question in Wire the question gate."]
    # The submit path asks it for questions only.
    assert submit._unwatched_by_now(_question(), SID) is False


def test_the_submit_recheck_routes_a_question_to_the_gate(monkeypatch):
    from agent_media_core.intake import submit

    seen = []
    monkeypatch.setattr(question_gate, "left_by_now",
                        lambda e, s: seen.append((e.text, s)) or True)
    assert submit._unwatched_by_now(_question(), SID) is True
    assert seen == [("Ship it? Yes, or no.", SID)]
