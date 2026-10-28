"""A project folder of its own in the reader (doc_read): ~/agent-config is
outside every root, and listed in MEDIA_PROJECT_DIRS it lists and opens its
documents by `p-agent-config`, `agent-config` or its path. Made up in tmp
dirs (David, 19 Oct 2026)."""

from __future__ import annotations

import pytest

from agent_media_core import layout
from agent_media_server import auth, doc_read


@pytest.fixture()
def single(tmp_path, monkeypatch):
    root = tmp_path / "projects"
    (root / "thing" / ".git").mkdir(parents=True)
    repo = tmp_path / "agent-config"
    (repo / ".git").mkdir(parents=True)
    (repo / "docs").mkdir()
    (repo / "README.md").write_text("# Agent config\n")
    (repo / "docs" / "notes.md").write_text("# Notes\n")
    (repo / ".claude").mkdir()
    (repo / ".claude" / "hidden.md").write_text("# Hidden\n")
    monkeypatch.setenv("MEDIA_PROJECTS_DIR", str(root))
    monkeypatch.setenv("MEDIA_DOC_ROOTS", "")
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("MEDIA_LAYOUT", layout.PROJECTS)
    monkeypatch.setenv("MEDIA_PROJECT_DIRS", str(repo))
    monkeypatch.setattr(layout, "_index_seen", (0.0, (), {}))
    monkeypatch.setattr(auth, "gate", lambda b: ({"username": "david"}, {}))
    return repo


@pytest.mark.parametrize("label,path", [("p-agent-config", ""), ("agent-config", ""),
                                        ("", "{repo}/docs")])
def test_a_folder_of_its_own_lists_its_documents(single, label, path):
    ok, out = doc_read.project_docs(label, path.format(repo=single), "good")
    assert ok, out
    assert out["root"] == str(single.resolve())
    assert [r["title"] for r in out["docs"]] == ["Agent config", "Notes"]


def test_its_documents_open_but_not_a_dot_dir_or_a_folder_not_listed(single, monkeypatch):
    assert doc_read.safe_path(str(single / "docs" / "notes.md"))
    assert doc_read.safe_path(str(single / ".claude" / "hidden.md")) is None
    monkeypatch.setenv("MEDIA_PROJECT_DIRS", "")
    assert doc_read.safe_path(str(single / "docs" / "notes.md")) is None
    ok, out = doc_read.project_docs("p-agent-config", "", "good")
    assert not ok and out["status"] == 404


def test_a_folder_of_its_own_turns_up_in_a_search(single, monkeypatch):
    """GET /search's `docs` covered only the projects folder: a document in
    ~/agent-config never turned up (agent-mail #136, David, 28 Oct 2026).
    Same refusals as the reader: not a dot-dir, not a folder not listed."""
    (single / "docs" / "notes.md").write_text("# Notes\n\nThe kettle sings.\n")
    (single / ".claude" / "hidden.md").write_text("# Hidden\n\nThe kettle sings.\n")
    hits = doc_read.search(["kettle", "sings"])
    assert [h["title"] for h in hits] == ["Notes"]
    assert hits[0]["project"] == "agent-config" and hits[0]["snippet"] == "The kettle sings."
    assert hits[0]["path"] == str(single.resolve() / "docs" / "notes.md") or hits[0]["path"].endswith("agent-config/docs/notes.md")
    monkeypatch.setenv("MEDIA_PROJECT_DIRS", "")
    assert doc_read.search(["kettle", "sings"]) == []


def test_and_without_rg(single, monkeypatch):
    (single / "docs" / "notes.md").write_text("# Notes\n\nThe kettle sings.\n")
    monkeypatch.setattr(doc_read, "_rg", lambda args, cwd: None)
    assert [h["title"] for h in doc_read.search(["kettle"])] == ["Notes"]
