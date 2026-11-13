"""Playing an unheard reply goes on through the thread's unheard ones after it.

With several replies waiting unheard in a thread, opening it (or pressing the
oldest one's Play) reads every one of them in order, not just the newest
(David, 16 Oct 2026). Each replay hands the next unheard row to its follower
as `--then-id` (tested in test_replay_question_then_reply.py), so a Stop ends
the run.
"""

from __future__ import annotations

import argparse
import time

import pytest

from agent_media_core import cli
from agent_media_core.state import StateStore


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    monkeypatch.setattr(cli, "_row_has_audio", lambda r: True)
    return StateStore()


def _row(st, text, session="s1", **ex):
    return st.add_history(sink="speech", uri="/x.mp3", started_at=time.time(),
                          target="phone", source="claude-code", text=text,
                          extras={"source_session": session, **ex})


def test_the_next_unheard_is_the_oldest_after_it_in_the_same_thread(store):
    first = _row(store, "one", held=True)
    _row(store, "elsewhere", session="s2", held=True)
    heard = _row(store, "heard", held=True)
    store.mark_heard(heard)
    _row(store, "spoken at once")                       # not held
    second = _row(store, "two", held=True)
    third = _row(store, "three", held=True)

    def next_of(rid):
        return cli._next_unheard(store.history_row(rid))

    assert next_of(first) == second
    assert next_of(second) == third
    assert next_of(third) is None


def test_only_an_unheard_reply_goes_on(store):
    played = _row(store, "played", held=True)
    store.mark_heard(played)
    plain = _row(store, "plain")
    _row(store, "waiting", held=True)
    assert cli._next_unheard(store.history_row(played)) is None
    assert cli._next_unheard(store.history_row(plain)) is None


def test_replay_by_id_chains_the_next_unheard(store, monkeypatch):
    first = _row(store, "one", held=True)
    second = _row(store, "two", held=True)
    calls = []
    monkeypatch.setattr(cli, "_replay_row",
                        lambda row, **kw: calls.append((row["id"], kw)) or 0)
    a = argparse.Namespace(id=first, from_sentence=None, again=False, lock_fd=7)
    assert cli.cmd_replay(a) == 0
    # The token handed down by the reply before it goes on to its replay.
    assert calls == [(first, {"then_id": second, "lock_fd": 7})]


def _unheard_ns(session):
    return argparse.Namespace(id=None, from_sentence=None, again=False,
                              unheard=True, session=session, index=1)


def _played(monkeypatch):
    calls = []
    monkeypatch.setattr(cli, "_replay_row",
                        lambda row, **kw: calls.append((row["id"], kw)) or 0)
    return calls


def test_replay_unheard_starts_at_the_first_of_those_the_thread_ends_on(store, monkeypatch):
    # A reply notification's Play (David, 2 Nov 2026): that thread's unheard
    # replies in order, never another thread's. A reply heard, or spoken at
    # once, ends the run back: the unheard one before it is not played.
    _row(store, "before", held=True)
    _row(store, "spoken at once")
    first = _row(store, "one", held=True)
    _row(store, "elsewhere", session="s2", held=True)
    played = _row(store, "played", held=True)
    store.mark_heard(played)
    second = _row(store, "two", held=True)
    third = _row(store, "three", held=True)
    calls = _played(monkeypatch)
    assert cli.cmd_replay(_unheard_ns("s1")) == 0
    assert calls == [(second, {"then_id": third})]
    assert first < second


def test_replay_unheard_starts_after_the_listeners_own_turn(store, monkeypatch):
    # What came before the listener's last message was answered, so read;
    # the app's "Play the unheard replies" counts the same (14 Nov 2026).
    _row(store, "answered", held=True)
    store.add_history(sink="speech", uri="", started_at=time.time(), target="phone",
                      source="listener", text="You: go ahead",
                      extras={"source_session": "s1"})
    first = _row(store, "one", held=True)
    second = _row(store, "two", held=True)
    calls = _played(monkeypatch)
    assert cli.cmd_replay(_unheard_ns("s1")) == 0
    assert calls == [(first, {"then_id": second})]


def test_replay_unheard_with_none_waiting_is_the_threads_newest(store, monkeypatch):
    played = _row(store, "played", held=True)
    store.mark_heard(played)
    asked = []
    monkeypatch.setattr(cli, "_do_replay",
                        lambda index, session=None: asked.append((index, session)) or 0)
    monkeypatch.setattr(cli, "_replay_row", lambda row, **kw: pytest.fail("no unheard row"))
    assert cli.cmd_replay(_unheard_ns("s1")) == 0
    assert asked == [(1, "s1")]
