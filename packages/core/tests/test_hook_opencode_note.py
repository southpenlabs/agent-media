"""`media-hook-opencode note`: what opencode's plugin attaches to a prompt
(and, with --restart, to a tool's result) — Claude Code's context notes."""

import io
import json
import sys
import time

import pytest

from agent_media_core.intake import hook_opencode

from test_harnesses_opencode import OC, SUB, add, make_db


@pytest.fixture
def env(tmp_path, monkeypatch):
    for var in list(__import__("os").environ):
        if var.startswith(("MEDIA_", "RELAY_")):
            monkeypatch.delenv(var)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    for var, sub in (("XDG_DATA_HOME", "data"), ("XDG_STATE_HOME", "state"),
                     ("XDG_CACHE_HOME", "cache"), ("XDG_CONFIG_HOME", "config")):
        monkeypatch.setenv(var, str(tmp_path / sub))
    monkeypatch.setenv("MEDIA_RELATED_THREADS", "0")
    db = make_db(tmp_path / "data" / "opencode" / "opencode.db")
    add(db, OC, messages=[
        ("user", {}, [{"type": "text", "text": "hello"}]),
        ("assistant", {"modelID": "big-pickle", "tokens": {
            "input": 150_000, "output": 10, "cache": {"read": 20_000, "write": 0}}},
         [{"type": "text", "text": "Hi."}]),
    ])
    add(db, SUB, parent=OC)
    return tmp_path


def note(capsys, monkeypatch, payload, *args):
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
    assert hook_opencode.main(["note", *args]) == 0
    return capsys.readouterr().out.strip()


def pend(tmp, sid):
    now = time.time()
    d = tmp / "state" / "agent-media"
    d.mkdir(parents=True, exist_ok=True)
    (d / "restart-pending.json").write_text(json.dumps({
        "id": "r1", "state": "waiting", "deadline": now + 600, "alive_until": now + 600,
        "waiting_on": [{"session": sid, "kind": "working"}]}))
    (d / "sessiond").mkdir(exist_ok=True)
    (d / "sessiond" / f"{sid}.json").write_text(json.dumps({"id": sid}))


def test_a_prompt_gets_the_notes_once_as_plain_text(env, capsys, monkeypatch):
    out = note(capsys, monkeypatch, {"session_id": OC, "prompt": "hello", "cwd": "/x"})
    assert "How to write for this user" in out
    assert OC in out  # the thread's link
    assert "85% full" in out  # 170k of the 200k guessed for its model
    assert not out.startswith("{")  # not hook JSON: the plugin attaches it as is
    again = note(capsys, monkeypatch, {"session_id": OC, "prompt": "more", "cwd": "/x"})
    assert "How to write" not in again and OC not in again


def test_a_sessiond_chat_is_told_of_a_waiting_restart(env, capsys, monkeypatch):
    pend(env, OC)
    monkeypatch.setenv("MEDIA_SOURCE_KIND", "headless")
    assert "end your turn" in note(capsys, monkeypatch, {"session_id": OC}, "--restart")
    # once per restart
    assert note(capsys, monkeypatch, {"session_id": OC}, "--restart") == ""


def test_a_subagent_gets_its_own_restart_note_through_its_parent(env, capsys, monkeypatch):
    pend(env, OC)
    monkeypatch.setenv("MEDIA_SOURCE_KIND", "headless")
    out = note(capsys, monkeypatch, {"session_id": SUB, "prompt": "do the task"})
    assert "hand back" in out and "How to write" not in out


def test_outside_sessiond_no_restart_note(env, capsys, monkeypatch):
    pend(env, OC)  # a TUI opencode: not the server's MEDIA_SOURCE_KIND=headless
    assert note(capsys, monkeypatch, {"session_id": OC}, "--restart") == ""


def test_bad_input_prints_nothing(env, capsys, monkeypatch):
    monkeypatch.setattr(sys, "stdin", io.StringIO("not json"))
    assert hook_opencode.main(["note"]) == 0
    assert capsys.readouterr().out == ""
