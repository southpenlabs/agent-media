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


# ---- a reply still being spoken has no row yet: its claim (3 Nov 2026) ------------
# David, 3 Nov 2026: a 56-second reply was still being read 45 s after the
# turn, so the history had nothing from the session, and a held copy was
# filed and played again when he opened the thread. The Stop hook claims a
# reply as it commits to it (core intake/reply_claims.py); sessiond reads it.

def _claim_as(monkeypatch, session, *, done, alive=True):
    from agent_media_core.intake import reply_claims

    monkeypatch.setattr(reply_claims, "_alive", lambda pid: alive)
    assert reply_claims.claim(reply_claims.key_of("All done."), session)
    if done:
        reply_claims.done(reply_claims.key_of("All done."))
    return reply_claims


def test_a_reply_handed_on_is_left_alone(monkeypatch):
    _claim_as(monkeypatch, "sid-1", done=True)
    assert _run(monkeypatch, []) == []


def test_a_reply_still_speaking_is_waited_for_then_left_alone(monkeypatch):
    rc = _claim_as(monkeypatch, "sid-1", done=False)
    sleeps = []

    def _sleep(s):
        sleeps.append(s)
        if len(sleeps) == 2:          # the reply ends while sessiond waits
            rc.done(rc.key_of("All done."))
    monkeypatch.setattr(sessiond.time, "sleep", _sleep)
    assert _run(monkeypatch, []) == []
    assert len(sleeps) >= 2


def test_a_hook_killed_before_handing_on_is_recovered(monkeypatch):
    _claim_as(monkeypatch, "sid-1", done=False, alive=False)
    assert len(_run(monkeypatch, [])) == 1


def test_another_sessions_claim_does_not_count(monkeypatch):
    _claim_as(monkeypatch, "sid-2", done=True)
    assert len(_run(monkeypatch, [])) == 1
