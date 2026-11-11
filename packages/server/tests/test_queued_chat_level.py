"""A new chat still waiting for a session slot takes a speech level and a pin
(David, 11 Nov 2026: "Not changed: no such session" on a queued new chat)."""
import json

from agent_media_server import auth, pins, sessiond, sessions

SID = "02f1d7da-9b67-49d0-b9bd-441cf7f4c3d9"


def _nothing_else_knows_it(monkeypatch, tmp_path):
    monkeypatch.setattr(auth, "gate", lambda bearer: ("david", None))
    monkeypatch.setattr(sessions, "live_sessions", lambda: {})
    monkeypatch.setattr(sessions, "session_exists", lambda s: False)
    monkeypatch.setattr(sessions, "_folder_for_session", lambda s: None)
    monkeypatch.setattr(sessiond, "state_root", lambda: tmp_path)


def test_a_queued_new_chat_takes_a_speech_level(monkeypatch, tmp_path):
    from agent_media_core import speak_priority

    _nothing_else_knows_it(monkeypatch, tmp_path)
    ok, err = pins.session_priority(SID, None, "tok", level="interrupt")
    assert not ok and err["status"] == 404
    (tmp_path / "held.json").write_text(json.dumps({"held": {SID: {"since": 1.0}}}))
    ok, got = pins.session_priority(SID, None, "tok", level="interrupt")
    assert ok and speak_priority.level_of(SID) == "interrupt"


def test_a_chat_with_a_sessiond_record_takes_a_pin(monkeypatch, tmp_path):
    _nothing_else_knows_it(monkeypatch, tmp_path)
    sessiond.record_path(SID, tmp_path).write_text("{}")
    ok, _ = pins.session_pin(SID, True, "tok")
    assert ok
