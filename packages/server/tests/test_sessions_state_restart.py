"""`/sessions/state` carries a waiting restart of the session host
(`restart_pending`, 4 Nov 2026), so the app's poll can say so on a thread."""

from __future__ import annotations

from agent_media_server import auth, sessiond, sessions


def _states(monkeypatch, view):
    monkeypatch.setattr(auth, "may_control_speech", lambda bearer: (True, {}))
    monkeypatch.setattr(sessions, "cached_states", lambda: ([], {}))
    monkeypatch.setattr(sessiond, "restart_view", lambda: view)
    return sessions.session_states("tok")


def test_a_waiting_restart_is_in_the_answer(monkeypatch):
    view = {"since": 1.0, "deadline": 1801.0, "state": "waiting",
            "waiting_on": [{"session": "a", "kind": "turn", "label": "x"}]}
    ok, out = _states(monkeypatch, view)
    assert ok and out["restart_pending"] == view


def test_none_waiting_leaves_it_out(monkeypatch):
    ok, out = _states(monkeypatch, None)
    assert ok and "restart_pending" not in out
