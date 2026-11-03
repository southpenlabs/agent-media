"""A question stopped because its thread left the screen as the black screen
came down (question_gate.cut_unanswerable) is held for its thread by the
history write of the process that was playing it, so opening the thread
reads it (David, 3 Nov 2026)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from agent_media_core import question_gate
from agent_media_core.intake import submit as S
from agent_media_core.intake import toast
from agent_media_core.state import StateStore
from agent_media_core.types import Event, Source

from test_earcons import _Coord, _render, _Sink

SID = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch):
    """Nothing here reaches a real player: a throwaway home, a fake sink."""
    for k in list(os.environ):
        if k.startswith(("MEDIA_", "RELAY_", "TMUX")):
            monkeypatch.delenv(k, raising=False)
    for d in ("HOME", "XDG_STATE_HOME", "XDG_CACHE_HOME", "XDG_CONFIG_HOME"):
        monkeypatch.setenv(d, str(tmp_path / d.lower()))
    monkeypatch.setenv("MEDIA_RENDER_ENGINE", "edge")
    monkeypatch.setenv("MEDIA_RENDER_VOICE", "en-US-AriaNeural")
    monkeypatch.setenv("MEDIA_SPEECH_DEFAULT_TARGET", "local")
    monkeypatch.setenv("MEDIA_SPEECH_LOCK_TIMEOUT_S", "10")
    monkeypatch.setenv("MEDIA_EARCONS", "0")
    monkeypatch.setattr(S, "render_text", _render)
    monkeypatch.setattr(S, "_clip_duration", lambda p: 0.0)
    monkeypatch.setattr(toast, "show", lambda where: None)
    monkeypatch.setattr(toast, "_tmux", lambda args: "")


def _ask(state, sink):
    return S.submit_event(
        Event(text="Ship it? Yes, or no.", source=Source.CLAUDE_CODE,
              metadata={"kind": "notif", "ask": True, "session": SID,
                        "tool_use_id": "toolu_x", "title": "Wire the gate"}),
        state=state, sink=sink, coordinator=_Coord())


def _last_row(state):
    return state.recent_history(sink="speech", limit=1)[0]


def test_a_cut_question_is_held_for_its_thread():
    state = StateStore()

    def cut(_n):
        np = state.get_now_playing("speech") or {}
        assert (np.get("extras") or {}).get("tool_use_id") == "toolu_x"
        question_gate._mark_cut(np["started_at"])

    _ask(state, _Sink(on_play=cut))
    ex = _last_row(state)["extras"]
    assert ex.get("held") and ex.get("dedup_key")
    assert not ex.get("heard")
    pending = [p for p in toast._pending(opened=True)]
    assert len(pending) == 1
    assert toast.take_for_opened(SID) == _last_row(state)["id"]


def test_a_question_heard_out_is_not_held():
    state = StateStore()
    _ask(state, _Sink())
    ex = _last_row(state)["extras"]
    assert not ex.get("held")
    assert list(toast._pending(opened=True)) == []
