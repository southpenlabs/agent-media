"""A chat that exits just after its turn may take its Stop hook with it
(David, 30 Oct 2026: "why is there no play button for this thread?" — the
session parked 5 s after its Stop on a thrashing host, and the reply was
never recorded). sessiond looks for the reply in the history and, missing,
runs the hook again to keep it held. Nothing here runs the real hook."""

from __future__ import annotations

import json
import time

import agent_media_core.state as core_state
from agent_media_server import sessiond


class _Store:
    def __init__(self, rows):
        self.rows = rows

    def recent_history(self, **k):
        return self.rows


def _run(monkeypatch, rows):
    ran = []
    monkeypatch.setattr(sessiond, "RECOVER_AFTER_S", 0.0)
    monkeypatch.setattr(core_state, "StateStore", lambda: _Store(rows))
    monkeypatch.setattr(sessiond.subprocess, "run", lambda argv, **k: ran.append((argv, k)))
    sessiond.Supervisor._recover_reply(None, "sid-1", "All done.", time.time() - 1, "/tmp", {"PATH": "/bin"})
    return ran


def test_a_recorded_reply_is_left_alone(monkeypatch):
    now = time.time()
    assert _run(monkeypatch, [{"started_at": now, "extras": {"session": "sid-1"}}]) == []


def test_a_missing_reply_is_kept_held(monkeypatch):
    now = time.time()
    rows = [{"started_at": now, "extras": json.dumps({"session": "other"})},
            {"started_at": now, "extras": {"session": "sid-1", "listener": True}},
            {"started_at": now - 600, "extras": {"session": "sid-1"}}]
    ran = _run(monkeypatch, rows)
    assert len(ran) == 1
    argv, k = ran[0]
    assert argv[-1] == "agent_media_core.intake.hook_claude_code"
    assert k["env"]["MEDIA_HOOK_HOLD"] == "1" and k["env"]["PATH"] == "/bin"
    assert json.loads(k["input"]) == {"hook_event_name": "Stop", "session_id": "sid-1",
                                      "cwd": "/tmp", "last_assistant_message": "All done."}
