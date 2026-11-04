"""A reply typed into a pane whose turn is running answers `queued: true`
(§6.3), as a headless send does, so the app marks the message queued."""
from __future__ import annotations

import pytest
from agent_media_server import panes, send, sessions

SID = "6c73a1f0-0000-4000-8000-000000000001"


@pytest.fixture
def pane(monkeypatch):
    typed = []
    monkeypatch.setattr(sessions, "live_sessions", lambda: {SID: "%42"})
    monkeypatch.setattr(sessions, "agent_of", lambda s: "claude")
    monkeypatch.setattr(panes, "alive", lambda p: True)
    monkeypatch.setattr(send, "for_pane", lambda body, *a: body)
    monkeypatch.setattr(send, "_send_to_pane", lambda p, body: typed.append(body) or "")
    monkeypatch.setattr(send, "_ensure_submitted", lambda *a, **kw: True)
    monkeypatch.setattr(send, "_record_turn", lambda *a: None)
    import agent_media_server.archive as archive
    import agent_media_server.rest as rest
    monkeypatch.setattr(archive, "unarchive_quietly", lambda s: None)
    monkeypatch.setattr(rest, "clear_quietly", lambda s: None)
    return typed


@pytest.mark.parametrize("state,queued", [("working", True), ("waiting", False), ("approval", False), (None, False)])
def test_pane_send_says_queued_while_a_turn_runs(monkeypatch, pane, state, queued):
    monkeypatch.setattr(sessions, "activity_of", lambda s, p, **kw: {"state": state})
    ok, detail = send._deliver_pane(SID, "hi", "hi")
    assert ok and pane == ["hi"]
    assert detail.get("queued", False) is queued


def test_unreadable_screen_is_not_queued(monkeypatch, pane):
    def boom(*a, **kw):
        raise RuntimeError("tmux gone")
    monkeypatch.setattr(sessions, "activity_of", boom)
    ok, detail = send._deliver_pane(SID, "hi", "hi")
    assert ok and "queued" not in detail
