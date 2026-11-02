"""A thread whose session has ended is not working, whatever its last turn
says. A turn is closed by its Stop hook; a chat parked by sessiond seconds
after its reply (exit 143) never ran it, and the phone said "working" under
an ended thread until the next message (David, 2 Nov 2026: "It says it's
working but the session is ended... why is that?")."""
from agent_media_core import activity

from agent_media_server import driver, sessions, threads

OPEN_TURN = {"since": 100.0, "count": 1, "current": "Read the restart result",
             "current_at": 101.0, "steps": ["Read the restart result"], "server_time": 102.0}
LINES = [{"start": 0.0, "end": 1.0, "who": "agent", "text": "Done.", "at": 90.0, "key": "k"}]


def _rig(monkeypatch, headless):
    monkeypatch.setattr(activity, "attach", lambda s, lines: dict(OPEN_TURN))
    monkeypatch.setattr(sessions, "live_sessions", lambda: {})
    monkeypatch.setattr(driver, "headless_state", lambda s: headless)
    monkeypatch.setattr(sessions, "suggestion_for", lambda *a, **k: "")


def test_an_ended_session_is_not_working(monkeypatch):
    _rig(monkeypatch, {"state": "ended", "live": False, "pane": None, "approval": None})
    env = threads._envelope("1950253e-395e-415b-b7c8-c37e5e2d2cae", list(LINES))
    assert env["working"] is None
    assert env["pending"] is False


def test_a_running_session_still_says_what_it_is_doing(monkeypatch):
    _rig(monkeypatch, {"state": "working", "live": True, "pane": None, "approval": None})
    env = threads._envelope("1950253e-395e-415b-b7c8-c37e5e2d2cae", list(LINES))
    assert env["working"]["current"] == "Read the restart result"
    assert env["pending"] is True
