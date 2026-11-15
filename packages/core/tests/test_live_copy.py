"""One live copy per session, found without tmux (#491 step 4)."""

import json
import subprocess

from agent_media_core import claude_sessions, live_copy, procinfo

SID = "027c24b1-a031-44a8-97e4-f890c45494d4"


def _write(tmp_path, pid, **data):
    (tmp_path / f"{pid}.json").write_text(json.dumps({"pid": pid, **data}))


def _setup(tmp_path, monkeypatch, envs=None):
    monkeypatch.setenv("CLAUDE_SESSIONS_DIR", str(tmp_path))
    monkeypatch.setattr(claude_sessions, "_is_claude", lambda pid: pid < 100)
    envs = envs or {}
    monkeypatch.setattr(procinfo, "environ", lambda pid: envs.get(pid, {}))


def test_records_keep_cwd_and_entrypoint(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    _write(tmp_path, 42, sessionId=SID, cwd="/w", entrypoint="cli", tmux="p:@1.%5")
    _write(tmp_path, 43, sessionId="other", cwd="/x", entrypoint="cli")
    _write(tmp_path, 143, sessionId="gone")                 # not a claude any more
    recs = sorted(claude_sessions.records(), key=lambda r: r["pid"])
    assert recs == [{"pid": 42, "session": SID, "pane": "%5", "cwd": "/w", "entrypoint": "cli"},
                    {"pid": 43, "session": "other", "pane": "", "cwd": "/x", "entrypoint": "cli"}]


def test_a_terminal_claude_holds_its_session(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch, envs={43: {"HOME": "/h"}})
    _write(tmp_path, 43, sessionId=SID, cwd="/w", entrypoint="cli")
    h = live_copy.holder(SID)
    assert h["where"] == "terminal" and h["pid"] == 43
    assert live_copy.describe(h) == "a terminal (pid 43)"
    assert live_copy.holder("someone-else") is None
    assert live_copy.holder(SID, exclude_pids=[43]) is None


def test_a_pane_comes_first_and_sessiond_is_skipped_when_asked(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch, envs={41: {"MEDIA_SESSIOND_SESSION": SID},
                                        42: {"TMUX_PANE": "%5"}})
    _write(tmp_path, 41, sessionId=SID, entrypoint="sdk-cli")
    _write(tmp_path, 42, sessionId=SID, entrypoint="cli", tmux="p:@1.%5")
    assert live_copy.holder(SID)["where"] == "pane"
    assert {h["where"] for h in live_copy.holders(SID)} == {"pane", "sessiond"}
    (tmp_path / "42.json").unlink()
    assert live_copy.holder(SID)["where"] == "sessiond"
    assert live_copy.holder(SID, count_sessiond=False) is None


def test_with_no_environment_the_entrypoint_tells_sessiond_apart(tmp_path, monkeypatch):
    """Windows: no process environment to read."""
    _setup(tmp_path, monkeypatch)
    _write(tmp_path, 41, sessionId=SID, entrypoint="sdk-cli")
    _write(tmp_path, 42, sessionId="desk", entrypoint="cli")
    assert live_copy.holder(SID)["where"] == "sessiond"
    assert live_copy.holder("desk")["where"] == "terminal"


def test_the_cli_answers_with_its_exit_code(tmp_path, monkeypatch, capsys):
    _setup(tmp_path, monkeypatch, envs={43: {"X": "1"}})
    _write(tmp_path, 43, sessionId=SID, entrypoint="cli")
    assert live_copy.main([SID]) == live_copy.HELD_RC
    assert "a terminal (pid 43)" in capsys.readouterr().out
    assert live_copy.main(["free-one", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["holders"] == []


def test_windows_process_table_from_cim(monkeypatch):
    rows = [{"ProcessId": 7, "Name": "claude.exe",
             "CommandLine": '"C:\\Users\\d\\.local\\bin\\claude.exe" --resume ' + SID},
            {"ProcessId": 8, "Name": "System", "CommandLine": None},
            {"ProcessId": "x"}]
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(
        a, 0, stdout=json.dumps(rows), stderr=""))
    got = dict(procinfo._windows_processes())
    assert got[7][0] == "C:\\Users\\d\\.local\\bin\\claude.exe"
    assert got[7][-1] == SID
    assert got[8] == ["System"]
    assert set(got) == {7, 8}
