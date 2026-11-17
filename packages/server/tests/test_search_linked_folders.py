"""search._claude_files reads a chat once when its project folder's old name
is a link to the new one (a repo moved, 17 Nov 2026)."""

from __future__ import annotations

from pathlib import Path

from agent_media_server import search

SID = "6ad5d425-2f0d-4ab0-aa12-99bfc9a1cd56"


def test_a_linked_project_folder_is_read_once(tmp_path, monkeypatch):
    from agent_media_core import harnesses

    projects = tmp_path / ".claude" / "projects"
    real = projects / "-home-x-spl-projects-agent-media"
    real.mkdir(parents=True)
    (real / f"{SID}.jsonl").write_text("{}\n")
    (projects / "-home-x-projects-agent-media").symlink_to(real)
    monkeypatch.setattr(harnesses, "_claude_dir", lambda: tmp_path / ".claude")
    monkeypatch.setattr(search, "_owned_elsewhere", lambda: set())
    got = [(Path(f).parent.name, s) for f, s in search._claude_files() if s == SID]
    assert got == [("-home-x-spl-projects-agent-media", SID)]
