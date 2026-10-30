"""`you_at` and `last_from_you` on /targets rows: Show → Sent and Waiting for
reply (David, 30 Oct 2026: "like Gmail's Sent label … with a Waiting for
reply option")."""
from __future__ import annotations

import json
import os
import uuid

import pytest

from agent_media_server import transcript, you_wrote

SID = "6c73498c-02c1-4846-8350-a82006973571"


@pytest.fixture(autouse=True)
def _fresh():
    you_wrote._reset_for_tests()
    transcript._reset_for_tests()
    yield
    you_wrote._reset_for_tests()


def _ts(t: float) -> str:
    from datetime import datetime, timezone

    return datetime.fromtimestamp(t, timezone.utc).isoformat().replace("+00:00", "Z")


def user(text: str, t: float) -> dict:
    return {"type": "user", "uuid": str(uuid.uuid4()), "timestamp": _ts(t),
            "message": {"role": "user", "content": text}}


def reply(text: str, t: float, stop: str = "end_turn") -> dict:
    return {"type": "assistant", "uuid": str(uuid.uuid4()), "timestamp": _ts(t),
            "message": {"role": "assistant", "stop_reason": stop,
                        "content": [{"type": "text", "text": text}]}}


def tool(t: float) -> dict:
    return {"type": "assistant", "uuid": str(uuid.uuid4()), "timestamp": _ts(t),
            "message": {"role": "assistant", "stop_reason": "tool_use",
                        "content": [{"type": "tool_use", "id": "t1", "name": "Bash",
                                     "input": {"command": "ls"}}]}}


@pytest.fixture()
def path(tmp_path):
    d = tmp_path / "claude" / "projects" / "-work"
    d.mkdir(parents=True)
    return d / f"{SID}.jsonl"


def write(path, *recs, mode="a"):
    with open(path, mode) as fh:
        for r in recs:
            fh.write(json.dumps(r, separators=(",", ":")) + "\n")
    # A later write in the same clock tick must still read as a change.
    st = os.stat(path)
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000))


def state(path):
    return you_wrote.of_session(SID, "claude", str(path))


def test_never_written_in(path):
    write(path, user("[From Cece] agent-mail #1 is for this session", 100),
          reply("Done.", 101))
    assert state(path) == (None, False)


def test_waiting_then_answered_then_waiting_again(path):
    write(path, user("hello there", 100))
    assert state(path) == (100.0, True)
    # The agent working on it: still unanswered.
    write(path, tool(101))
    assert state(path) == (100.0, True)
    write(path, {"type": "user", "uuid": str(uuid.uuid4()), "timestamp": _ts(101.5),
                 "message": {"role": "user", "content": [
                     {"type": "tool_result", "tool_use_id": "t1", "content": "a b"}]}},
          reply("Hi.", 102))
    assert state(path) == (100.0, False)
    # Another session's message and the host's notes are not the listener's.
    write(path, user('<cross-session-message from="x" from-name="agent-media-7">ping</cross-session-message>', 103),
          user("[From the session host] A restart is waiting", 104))
    assert state(path) == (100.0, False)
    write(path, user("one more thing", 110))
    assert state(path) == (110.0, True)


def test_first_sight_reads_back_to_the_listeners_prompt(path):
    write(path, user("the question", 100), reply("an answer", 101),
          *[user(f"[From Cece] mail {i}", 102 + i) for i in range(5)],
          reply("mail handled", 120))
    assert state(path) == (100.0, False)
    you_wrote._reset_for_tests()
    write(path, user("[From Cece] more mail", 121))
    assert state(path) == (100.0, False)


def test_a_rewritten_file_is_read_again(path):
    write(path, user("first", 100))
    assert state(path) == (100.0, True)
    write(path, user("[From Cece] mail", 50), reply("ok", 51), mode="w")
    assert state(path) == (None, False)


def test_a_chat_waits_while_its_newest_message_is_yours():
    assert you_wrote.of_chat([]) == (None, False)
    assert you_wrote.of_chat([{"role": "assistant", "at": 1}]) == (None, False)
    assert you_wrote.of_chat([{"role": "user", "at": 1}, {"role": "assistant", "at": 2}]) == (1.0, False)
    assert you_wrote.of_chat([{"role": "assistant", "at": 1}, {"role": "user", "at": 3}]) == (3.0, True)


def test_targets_rows_carry_both(monkeypatch, path):
    from agent_media_server import sessions

    write(path, user("are you there", 100), reply("Yes.", 101), user("good", 200))
    monkeypatch.setattr(sessions, "live_sessions", lambda: {SID: "%1"})
    monkeypatch.setattr(sessions, "_pane_titles", lambda: {"%1": "Work"})
    monkeypatch.setattr(sessions, "_recent_conversations", lambda: [])
    monkeypatch.setattr(sessions, "_agent_of_pane", lambda p: "claude")
    row = next(r for r in sessions.sessions_index() if r["session"] == SID)
    assert (row["you_at"], row["last_from_you"]) == (200.0, True)
