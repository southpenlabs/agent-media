"""The "you have mail" line in every harness's note (context._mail_note,
agent-mail #520): agent mail writes it, agent-media carries it."""

from __future__ import annotations

import json
import os
import stat

from agent_media_core.intake import context


def _fake_hook(tmp_path, monkeypatch, line="[agent-mail] 1 new unclaimed message: #7 from sam."):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    seen = tmp_path / "stdin.json"
    exe = bin_dir / "agent-mail-inbox-hook"
    exe.write_text(f"#!/bin/sh\n[ \"$1\" = --new ] || exit 3\ncat > {seen}\necho '{line}'\n")
    exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")
    monkeypatch.setenv("MEDIA_NOTE_MAIL", "1")
    return seen


def test_another_harness_gets_the_line_with_its_session_and_folder(tmp_path, monkeypatch):
    seen = _fake_hook(tmp_path, monkeypatch)
    got = context._mail_note({"session_id": "ses_abc", "cwd": "/w/proj"})
    assert got == "[agent-mail] 1 new unclaimed message: #7 from sam."
    assert json.loads(seen.read_text()) == {"session_id": "ses_abc", "cwd": "/w/proj"}


def test_claude_runs_agent_mails_own_hook_so_gets_nothing_here(tmp_path, monkeypatch):
    seen = _fake_hook(tmp_path, monkeypatch)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "cc"))
    hook = {"session_id": "s1", "cwd": "/w",
            "transcript_path": str(tmp_path / "cc" / "projects" / "-w" / "s1.jsonl")}
    assert context._mail_note(hook) == "" and not seen.exists()
    hook["transcript_path"] = "/home/x/.claude/projects/-w/s1.jsonl"
    assert context._mail_note(hook) == ""
    # Codex's transcript is its own.
    hook["transcript_path"] = "/home/x/.codex/sessions/2026/11/15/rollout-1.jsonl"
    assert context._mail_note(hook).startswith("[agent-mail]")


def test_switched_off_without_agent_mail_or_a_session_it_is_silent(tmp_path, monkeypatch):
    _fake_hook(tmp_path, monkeypatch)
    assert context._mail_note({"cwd": "/w"}) == ""
    monkeypatch.setenv("MEDIA_NOTE_MAIL", "0")
    assert context._mail_note({"session_id": "s", "cwd": "/w"}) == ""
    monkeypatch.setenv("MEDIA_NOTE_MAIL", "1")
    monkeypatch.setenv("PATH", "/nonexistent")
    assert context._mail_note({"session_id": "s", "cwd": "/w"}) == ""
