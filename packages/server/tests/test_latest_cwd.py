"""sessions.latest_cwd: the folder a session is in now, for its git chip."""

import json

from agent_media_server import moves, recaps, sessions


def _write(path, cwds):
    path.write_text("".join(json.dumps({"type": "user", "cwd": c}) + "\n" for c in cwds)
                    + json.dumps({"type": "summary"}) + "\n")


def test_latest_cwd_follows_the_transcript_tail(tmp_path, monkeypatch):
    t = tmp_path / "s.jsonl"
    _write(t, ["/repo", "/repo", "/repo/.worktrees/fix"])
    monkeypatch.setattr(recaps, "transcript_path", lambda s: str(t))
    monkeypatch.setattr(moves, "moved_cwd", lambda s: "")
    sessions._LATEST.clear()
    assert sessions.latest_cwd("abc") == "/repo/.worktrees/fix"
    _write(t, ["/repo", "/repo/.worktrees/fix", "/repo", "/repo"])
    assert sessions.latest_cwd("abc") == "/repo"


def test_latest_cwd_prefers_a_move_and_falls_back(tmp_path, monkeypatch):
    t = tmp_path / "s.jsonl"
    t.write_text(json.dumps({"type": "summary"}) + "\n")
    monkeypatch.setattr(recaps, "transcript_path", lambda s: str(t))
    monkeypatch.setattr(sessions, "session_cwd", lambda s: "/first")
    monkeypatch.setattr(moves, "moved_cwd", lambda s: "")
    sessions._LATEST.clear()
    assert sessions.latest_cwd("abc") == "/first"
    monkeypatch.setattr(moves, "moved_cwd", lambda s: "/moved")
    assert sessions.latest_cwd("abc") == "/moved"
