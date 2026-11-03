"""A reply read twice (David, 3 Nov 2026: "Fix it and enquire about the music
and lack of follow along too ... but also mark the reply with a resume
position").

Session 5447e280's 56-second reply had no history row until it had been
spoken, so the Stop hook's dedup and sessiond's recovery (45 s after the
turn) both missed it, and a held copy was filed; opening the thread played
that copy, with no music under it and no bold. Here: the reply's claim
(intake/reply_claims.py), its resume position across copies
(reply_position.py), and the held copy's replay. Nothing plays: every
sink is a stand-in.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import time

from agent_media_core import reply_position as RP
from agent_media_core.intake import reply_claims as RC

KEY = hashlib.sha1(b"The bridge upkeep will move.").hexdigest()


# ---- the claim -------------------------------------------------------------------

def test_one_claim_per_reply_until_its_process_is_gone(monkeypatch):
    assert RC.claim(KEY, "s1") is True
    assert RC.standing(KEY)
    assert RC.claim(KEY, "s1") is False                # a second Stop stands down
    assert RC.for_session("s1", time.time() - 60) == {"done": 0, "working": 1}
    # The hook killed before it handed the reply on: no longer counts.
    monkeypatch.setattr(RC, "_alive", lambda pid: False)
    assert not RC.standing(KEY)
    assert RC.for_session("s1", time.time() - 60) == {"done": 0, "working": 0}
    assert RC.claim(KEY, "s1") is True                 # taken over


def test_a_done_claim_stands_for_its_ttl_whatever_happens_to_its_process(monkeypatch):
    RC.claim(KEY, "s1")
    RC.done(KEY)
    monkeypatch.setattr(RC, "_alive", lambda pid: False)
    assert RC.standing(KEY)
    assert RC.for_session("s1", time.time() - 60) == {"done": 1, "working": 0}
    assert RC.for_session("s2", time.time() - 60) == {"done": 0, "working": 0}
    assert RC.for_session("s1", time.time() + 60) == {"done": 0, "working": 0}
    monkeypatch.setattr(RC.time, "time", lambda: time.monotonic() + 10 ** 10)
    assert not RC.standing(KEY)


def test_a_released_claim_can_be_taken_again():
    RC.claim(KEY, "s1")
    RC.release(KEY)
    assert not RC.standing(KEY)
    assert RC.claim(KEY, "s1") is True


def test_the_hook_finds_a_reply_still_being_spoken(monkeypatch):
    """No history row yet (it is written when playback ends): the claim."""
    from agent_media_core.intake import hook_claude_code as H
    from agent_media_core.state import StateStore

    text = "The bridge upkeep will move."
    assert H._dedup_seen(StateStore(), text) is False
    RC.claim(RC.key_of(text), "s1")
    assert H._dedup_seen(StateStore(), text) is True


def test_a_second_stop_for_the_same_reply_is_not_submitted(monkeypatch):
    from agent_media_core.intake import hook_claude_code as H
    from agent_media_core.types import Event, Source

    submitted = []
    monkeypatch.setattr(H, "submit_event", lambda e, state=None: submitted.append(e.text))
    monkeypatch.setattr(H, "_private", lambda s: False)
    ev = lambda: Event(text="All done here.", source=Source.CLAUDE_CODE,  # noqa: E731
                       metadata={"session": "s1"})
    H._play_now(ev())
    assert submitted == ["All done here."]
    rec = json.loads((RC._dir() / RC.key_of("All done here.")).read_text())
    assert rec["done"] is True and rec["session"] == "s1"
    H._play_now(ev())                                  # sessiond's recovery, say
    assert submitted == ["All done here."]


def test_a_reply_that_failed_gives_its_claim_back(monkeypatch):
    from agent_media_core.intake import hook_claude_code as H
    from agent_media_core.types import Event, Source

    def boom(e, state=None):
        raise RuntimeError("render failed")
    monkeypatch.setattr(H, "submit_event", boom)
    monkeypatch.setattr(H, "_private", lambda s: False)
    try:
        H._play_now(Event(text="Broken.", source=Source.CLAUDE_CODE, metadata={"session": "s1"}))
    except RuntimeError:
        pass
    assert not RC.standing(RC.key_of("Broken."))


# ---- the resume position ---------------------------------------------------------

def test_how_far_one_row_was_heard():
    assert RP.heard_to({}) is None                                  # played, never stopped
    assert RP.heard_to({"heard": True}) is None
    assert RP.heard_to({"held": True, "muted": True}) == 0
    assert RP.heard_to({"held": True, "heard": True}) is None
    assert RP.heard_to({"muted": True}) == 0
    assert RP.heard_to({"flushed": True}) == 0
    assert RP.heard_to({"stopped_at": {"sentence": 3, "at": 1.0}}) == 3
    assert RP.heard_to({"held": True, "stopped_at": {"sentence": 2}}) == 2


def _row(rid, **ex):
    return {"id": rid, "extras": {"dedup_key": KEY, "source_session": "s1", **ex}}


def test_the_reply_is_heard_when_any_copy_was():
    held = _row(14, held=True, muted=True)
    spoken = _row(15)
    other = {"id": 16, "extras": {"dedup_key": KEY, "source_session": "s2"}}
    assert RP.heard_to_any(held, [held]) == 0
    assert RP.heard_to_any(held, [spoken, held]) is None
    # Another thread's reply with the same words is not a copy.
    assert RP.heard_to_any(held, [other, held]) == 0
    stopped = _row(15, stopped_at={"sentence": 4})
    assert RP.heard_to_any(held, [stopped, held]) == 4
    assert RP.resume_from(held, [stopped, held]) == 4
    assert RP.resume_from(held, [spoken, held]) == 0      # heard: a play starts over


# ---- the thread's open -------------------------------------------------------------

def _held_and_spoken(st, *, spoken_ex=None):
    text = "The bridge upkeep will move."
    base = {"session": "s1", "source_session": "s1", "dedup_key": KEY}
    spoken = st.add_history(sink="speech", uri="/a.mp3", started_at=time.time() - 60,
                            target="phone", source="claude-code", text=text,
                            extras={**base, **(spoken_ex or {})})
    held = st.add_history(sink="speech", uri="/b.mp3", started_at=time.time() - 30,
                          target="phone", source="claude-code", text=text,
                          extras={**base, "held": True, "muted": True})
    return spoken, held


def _open_toast(monkeypatch, text="The bridge upkeep will move."):
    from agent_media_core.intake import toast
    from agent_media_core.types import Event, Priority, Source

    monkeypatch.setenv("MEDIA_TOAST_RENDER_WAIT_S", "0")
    monkeypatch.setattr(toast, "_tmux", lambda args: "")
    toast.hold(Event(text=text, source=Source.CLAUDE_CODE, priority=Priority.NORMAL,
                     metadata={"kind": "stop", "session": "s1"}))
    return toast


def test_a_held_copy_of_a_reply_already_heard_is_not_played_on_the_open(monkeypatch):
    from agent_media_core.state import StateStore

    st = StateStore()
    toast = _open_toast(monkeypatch)
    _spoken, held = _held_and_spoken(st)
    assert toast.take_for_opened("s1") is None
    assert st.history_row(held)["extras"]["heard"] is True


def test_a_held_copy_of_a_reply_stopped_part_way_is_played_from_there(monkeypatch):
    from agent_media_core import cli
    from agent_media_core.state import StateStore

    st = StateStore()
    toast = _open_toast(monkeypatch)
    _spoken, held = _held_and_spoken(st, spoken_ex={"stopped_at": {"sentence": 2}})
    assert toast.take_for_opened("s1") == held
    row = st.history_row(held)
    row["extras"]["clip_uris"] = ["/1", "/2", "/3", "/4"]
    row["extras"]["clip_sentences"] = ["a", "b", "c", "d"]
    monkeypatch.setattr(cli, "replay_sentence_map", lambda r: ["a", "b", "c", "d"])
    assert cli._resume_start(row) == 2


def test_replay_resume_starts_at_the_resume_position(monkeypatch):
    from agent_media_core import cli
    from agent_media_core.state import StateStore

    st = StateStore()
    rid = st.add_history(sink="speech", uri="/a.mp3", started_at=time.time(),
                         target="phone", source="claude-code", text="x",
                         extras={"source_session": "s1", "dedup_key": KEY,
                                 "held": True, "stopped_at": {"sentence": 1}})
    calls = []
    monkeypatch.setattr(cli, "_speech_history", lambda *a, **k: [])
    monkeypatch.setattr(cli, "replay_sentence_map", lambda r: ["a", "b", "c"])
    monkeypatch.setattr(cli, "_replay_row", lambda row, **kw: calls.append(kw) or 0)
    a = argparse.Namespace(again=False, id=rid, from_sentence=None, resume=True,
                           lock_fd=-1, unheard=False, session=None, index=1)
    assert cli.cmd_replay(a) == 0
    assert calls[-1].get("from_sentence") == 1
    a.resume = False
    cli.cmd_replay(a)
    assert "from_sentence" not in calls[-1]


def test_the_unheard_chain_skips_a_copy_already_heard(monkeypatch):
    from agent_media_core import cli
    from agent_media_core.state import StateStore

    st = StateStore()
    _spoken, held = _held_and_spoken(st)
    rows = [st.history_row(held)]
    assert cli._not_heard_elsewhere(rows) == []
    assert st.history_row(held)["extras"]["heard"] is True


# ---- the music under the held copy's replay ----------------------------------------

def test_a_held_reply_asks_for_music_under_its_replay(monkeypatch):
    """A held reply never went through submit's bed, so it recorded none:
    `_replay_under` stopped there and the replay had no music, where its
    spoken copy had a song under it. It now hands the row on for
    music_bed_replay.resolve to choose afresh."""
    from agent_media_core import cli
    from agent_media_core.types import Target

    monkeypatch.setattr(cli, "_socket_for", lambda t: "/tmp/mpv-test.sock")
    ex = {"held": True, "muted": True, "cwd": "/home/x/projects/p",
          "clip_uris": ["/a", "/b"], "clip_durations_s": [30.0, 30.0],
          "clip_sentences": ["a", "b"]}
    data = json.loads(cli._replay_under(ex, Target("local"), object()))
    assert data["ex"] == {"cwd": "/home/x/projects/p", "clip_uris": ["/a", "/b"],
                          "clip_durations_s": [30.0, 30.0]}
    assert cli._replay_under({"listener": True, "cwd": "/x"}, Target("local"), object()) == ""
    assert cli._replay_under({"clip_uris": ["/a"]}, Target("local"), object()) == ""


def test_a_held_reply_on_a_phone_is_given_a_bed_now(monkeypatch):
    from agent_media_core import cli, music_bed_replay
    from agent_media_core.types import Target

    monkeypatch.setattr(cli, "_socket_for", lambda t: "tcp://phone:7000")
    asked, started = [], []
    monkeypatch.setattr(music_bed_replay, "resolve",
                        lambda ex, t: asked.append(ex) or {"tone": "reply-E"})
    monkeypatch.setattr(music_bed_replay, "start",
                        lambda spec, t, sink: started.append(spec) or (True, None))
    ex = {"held": True, "cwd": "/home/x/projects/p", "clip_uris": ["/a"]}
    assert cli._replay_under(ex, Target("phone"), object()) == ""
    assert asked == [ex] and started == [{"tone": "reply-E"}]
