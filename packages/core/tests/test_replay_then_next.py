"""Keep reading after a send, redeemed while the next reply speaks.

David, 3 Nov 2026: "continue reading should be redeemable even after the
next reply is in progress. It should then play the next reply after it has
continued to the end." The app replays the reply the send cut, from where it
was cut, with `then: "next"` (`media replay --id A --from-sentence N
--then-next <thread>`). The replay holds the token at `_UNREAD_RANK`, so
the reply speaking (a question included) steps aside at the end of its
sentence; at the end of the replay, a reply of the thread still waiting for
the voice plays on from where it was, and otherwise the thread's next reply
not heard to its end is replayed from where it was left.

Fakes only: no player, no phone; the state dir is the test's own.
"""

from __future__ import annotations

import argparse
import os
import time

import pytest

from agent_media_core import cli
from agent_media_core.intake import submit as S
from agent_media_core.state import StateStore
from agent_media_core.types import Priority

SID = "45628bf4-a304-47e8-bbc2-83ebd1ceadff"
OTHER = "9bce6871-1164-43c7-bb6f-6064758ccae8"


@pytest.fixture(autouse=True)
def state_env(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))


def _row(session: str = SID, **extras) -> int:
    ex = {"source_session": session, "session": session, "kind": "stop",
          "clip_uris": ["/c/0.mp3", "/c/1.mp3", "/c/2.mp3"],
          "clip_durations_s": [2.0, 2.0, 2.0],
          "clip_sentences": ["One.", "Two.", "Three."], **extras}
    return StateStore().add_history(sink="speech", uri="/c/0.mp3",
                                    started_at=time.time(), target="phone",
                                    source="claude-code", text="One. Two. Three.",
                                    extras=ex)


def _waiter(rank: int, speaker: str) -> S._SpeechPlaybackLock:
    w = S._SpeechPlaybackLock(speaker=speaker)
    w._rank, w._session, w._seq = rank, "%7", time.time()
    w._register()
    return w


# ---- what follows -------------------------------------------------------------------

def test_the_reply_that_cut_it_and_is_waiting_goes_next():
    """The next reply was speaking for the first time and stepped aside for
    the replay: it has no row yet, and it picks up where it was once the
    replay lets go — nothing is started in its place."""
    a = _row(held=True, muted=True, stopped_at={"sentence": 1, "at": 1.0})
    _row(held=True, muted=True)             # an older unheard one after A
    w = _waiter(S._PRIO_RANK[Priority.NORMAL], SID)
    try:
        assert cli._next_to_resume(SID, a) is None
    finally:
        w._unregister()


def test_a_reply_of_another_thread_waiting_does_not_count():
    a = _row(stopped_at={"sentence": 1, "at": 1.0})
    b = _row(held=True, muted=True)
    w = _waiter(S._PRIO_RANK[Priority.NORMAL], OTHER)
    try:
        assert cli._next_to_resume(SID, a) == b
    finally:
        w._unregister()


def test_the_oldest_reply_after_it_not_heard_to_its_end():
    """Heard to the end is passed over; stopped part way or held and never
    played is next, the oldest first; another thread's never."""
    a = _row(stopped_at={"sentence": 1, "at": 1.0})
    _row(heard=True)
    _row(session=OTHER, held=True, muted=True)
    b = _row(stopped_at={"sentence": 2, "at": 2.0})
    _row(held=True, muted=True)
    assert cli._next_to_resume(SID, a) == b


def test_nothing_follows_when_every_later_reply_was_heard():
    a = _row(stopped_at={"sentence": 1, "at": 1.0})
    _row(heard=True)
    _row()                                  # played live before `heard` was kept
    assert cli._next_to_resume(SID, a) is None
    assert cli._next_to_resume(SID, _row(heard=True)) is None


# ---- the replay ---------------------------------------------------------------------

def test_replay_then_next_defers_the_choice_to_the_end(monkeypatch):
    """`--then-next` hands the thread down instead of an id chosen now: the
    reply that cut this one has no row while it is still speaking."""
    a = _row(held=True, muted=True, stopped_at={"sentence": 1, "at": 1.0})
    _row(held=True, muted=True)             # what _next_unheard would pick
    got: list = []
    monkeypatch.setattr(cli, "_replay_row",
                        lambda row, **kw: got.append((row["id"], kw)) or 0)
    ns = argparse.Namespace(again=False, id=a, from_sentence=1, resume=False,
                            then_next=SID, lock_fd=-1, unheard=False,
                            session=None, index=1)
    assert cli.cmd_replay(ns) == 0
    assert got == [(a, {"then_next": SID, "from_sentence": 1})]


def test_the_cli_takes_then_next():
    ns = cli._build_parser().parse_args(
        ["replay", "--id", "7", "--from-sentence", "2", "--then-next", SID])
    assert (ns.id, ns.from_sentence, ns.then_next) == (7, 2, SID)
    ns = cli._build_parser().parse_args(
        ["replay-track", "--then-next", SID, "--then-after", "7"])
    assert (ns.then_next, ns.then_after) == (SID, 7)


def test_a_keep_reading_replay_outranks_a_question_and_waits_for_a_sentence(monkeypatch):
    """The reply speaking, a question included, steps aside for it at the end
    of its sentence (the token at `_UNREAD_RANK`), and it waits that long."""
    a = _row(heard=True, stopped_at={"sentence": 1, "at": 1.0})
    asked: list = []
    pushed: list = []

    def take_within(self, wait_s, *, rank, session):
        asked.append((wait_s, rank))
        return False

    monkeypatch.setattr(S._SpeechPlaybackLock, "take_within", take_within)
    monkeypatch.setattr(cli, "_replay_audio_missing", lambda *a, **k: "")
    monkeypatch.setattr(cli, "_stop_replay_trackers", lambda: None)
    monkeypatch.setattr(cli, "_caller_pane", lambda: "")
    monkeypatch.setattr(cli, "_push_replay",
                        lambda *a, **kw: pushed.append(kw) or 0)
    row = StateStore().history_row(a)
    assert cli._replay_row(row, from_sentence=1, then_next=SID) == 0
    assert asked == [(cli._KEEP_READING_WAIT_S, S._UNREAD_RANK)]
    assert S._UNREAD_RANK > S._PRIO_RANK[Priority.HIGH]
    assert pushed[-1]["then_next"] == SID and pushed[-1]["rank"] == S._UNREAD_RANK
    # Without it, as before.
    asked.clear()
    assert cli._replay_row(row, from_sentence=1) == 0
    assert asked == [(cli._replay_wait_s(), S._REPLAY_RANK)]
    assert pushed[-1]["then_next"] is None


# ---- the follower at the end -------------------------------------------------------

def _follow_to_end(monkeypatch, **extra) -> list:
    StateStore().set_now_playing("speech", uri="/q.mp3", started_at=time.time(),
                                 target="phone", extras={})
    monkeypatch.setattr(cli.time, "sleep", lambda s: None)
    monkeypatch.setattr(cli, "_sock", lambda: "/s")
    it = iter([{"idle-active": False, "playlist-pos": 0, "time-pos": t,
                "pause": False, "speed": 1.0, "mute": False} for t in (1.0, 4.2)])
    monkeypatch.setattr(cli.ipc, "get_properties",
                        lambda sock, props: next(it, {"idle-active": True}))
    monkeypatch.setattr(cli.ipc, "get_property", lambda sock, prop: True)
    child: list = []

    def popen(argv, **kw):
        child.append((argv, [os.dup(fd) for fd in kw.get("pass_fds") or ()]))

    monkeypatch.setattr(cli.subprocess, "Popen", popen)
    lock = S._SpeechPlaybackLock(kind="replay")
    assert lock.take_within(1.0, rank=S._UNREAD_RANK, session="replay:test")
    assert cli.cmd_replay_track(argparse.Namespace(
        sentences="", offsets="", pane="", durations="[5.0]",
        lock_fd=lock.fileno, rank=S._UNREAD_RANK, then_id=None, **extra)) == 0
    for _argv, fds in child:
        for fd in fds:
            os.close(fd)
    return [argv for argv, _fds in child]


def test_played_to_its_end_it_goes_on_to_the_threads_next_reply(monkeypatch):
    a = _row(heard=True)
    b = _row(stopped_at={"sentence": 2, "at": 2.0})
    started = _follow_to_end(monkeypatch, then_next=SID, then_after=a)
    assert len(started) == 1
    assert started[0][3:7] == ["replay", "--resume", "--id", str(b)]


def test_played_to_its_end_it_lets_the_waiting_reply_speak(monkeypatch):
    a = _row(heard=True)
    _row(stopped_at={"sentence": 2, "at": 2.0})
    w = _waiter(S._PRIO_RANK[Priority.HIGH], SID)
    try:
        assert _follow_to_end(monkeypatch, then_next=SID, then_after=a) == []
    finally:
        w._unregister()
    # Nothing was handed the token: it goes free as the follower exits.
