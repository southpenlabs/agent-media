"""A message sessiond is holding is marked `held` in the thread (threads.mark_held)."""
import json

from agent_media_server import sessiond, threads


def _msg(role, text, at=1.0):
    return {"id": f"line:{at}", "role": role, "at": at,
            "parts": [{"type": "text", "text": text}], "spoken": None,
            "turn": {"running": False}}


def _record(monkeypatch, tmp_path, session, held):
    monkeypatch.setattr(sessiond, "state_root", lambda: tmp_path)
    monkeypatch.setattr(sessiond, "restart_view", lambda: None)
    (tmp_path / f"{session}.json").write_text(json.dumps({"id": session, "unstarted": True,
                                                          "held": held}))


def test_held_first_message_is_marked_busy(monkeypatch, tmp_path):
    _record(monkeypatch, tmp_path, "s1", [{"uuid": "u", "text": "Page: x\n\nhello   there", "at": 2.0}])
    msgs = [_msg("user", "hello there", 2.0)]
    threads.mark_held("s1", msgs)
    assert msgs[0]["held"] == "busy"


def test_restart_pending_says_restart(monkeypatch, tmp_path):
    _record(monkeypatch, tmp_path, "s2", [{"uuid": "u", "text": "hi", "at": 2.0}])
    monkeypatch.setattr(sessiond, "restart_view", lambda: {"state": "waiting"})
    msgs = [_msg("user", "hi")]
    threads.mark_held("s2", msgs)
    assert msgs[0]["held"] == "restart"


def test_only_the_newest_copy_and_never_a_reply(monkeypatch, tmp_path):
    _record(monkeypatch, tmp_path, "s3", [{"uuid": "u", "text": "again", "at": 5.0}])
    msgs = [_msg("user", "again", 1.0), _msg("assistant", "again", 2.0), _msg("user", "again", 3.0)]
    threads.mark_held("s3", msgs)
    assert [m.get("held") for m in msgs] == [None, None, "busy"]


def test_nothing_held_marks_nothing(monkeypatch, tmp_path):
    _record(monkeypatch, tmp_path, "s4", [])
    msgs = [_msg("user", "hi")]
    threads.mark_held("s4", msgs)
    assert "held" not in msgs[0]
    threads.mark_held("no-record", msgs)
    assert "held" not in msgs[0]
