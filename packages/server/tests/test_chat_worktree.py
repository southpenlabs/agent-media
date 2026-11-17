"""A worktree per chat in an opted-in repo (chat_worktree.py)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from agent_media_server import chat_worktree, headless_mail


def _git(*args: str, cwd: Path) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True,
                          text=True).stdout.strip()


@pytest.fixture
def repo(tmp_path, monkeypatch):
    top = tmp_path / "proj"
    top.mkdir()
    _git("init", "-q", "-b", "main", cwd=top)
    _git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty",
         "-m", "first", cwd=top)
    (top / "node_modules").mkdir()
    (top / "build").mkdir()
    monkeypatch.delenv("MEDIA_CHAT_WORKTREE_LINKS", raising=False)
    monkeypatch.setenv("MEDIA_CHAT_WORKTREES", str(top))
    return top


SID = "12345678-aaaa-bbbb-cccc-1234567890ab"


def test_fresh_chat_in_opted_in_repo_gets_its_own_worktree(repo):
    cwd = chat_worktree.make(str(repo), SID)
    dest = repo / ".worktrees" / "chat-12345678"
    assert cwd == str(dest)
    assert _git("rev-parse", "--abbrev-ref", "HEAD", cwd=dest) == "chat/12345678"
    assert (dest / "node_modules").is_symlink()
    assert (dest / "node_modules").resolve() == (repo / "node_modules").resolve()
    assert not (dest / "build").exists()
    assert "/.worktrees/" in (repo / ".git" / "info" / "exclude").read_text()
    # The same chat started again reuses it.
    assert chat_worktree.make(str(repo), SID) == str(dest)


def test_extra_links_and_no_escape(repo, monkeypatch):
    monkeypatch.setenv("MEDIA_CHAT_WORKTREE_LINKS", "build:../outside")
    dest = Path(chat_worktree.make(str(repo), SID))
    assert (dest / "build").is_symlink()
    assert chat_worktree.links() == ["node_modules", "build"]


def test_not_opted_in_or_subfolder_stays(repo, tmp_path, monkeypatch):
    sub = repo / "sub"
    sub.mkdir()
    assert chat_worktree.make(str(sub), SID) == str(sub)
    monkeypatch.setenv("MEDIA_CHAT_WORKTREES", "")
    assert chat_worktree.make(str(repo), SID) == str(repo)
    assert not (repo / ".worktrees").exists()


def test_git_failure_leaves_the_chat_where_it_asked(repo):
    _git("branch", "chat/12345678", cwd=repo)  # the branch is taken
    assert chat_worktree.make(str(repo), SID) == str(repo)


def test_owner_and_mail_by_folder(repo):
    dest = chat_worktree.make(str(repo), SID)
    assert chat_worktree.owner(dest) == str(repo.resolve())
    assert chat_worktree.owner(str(repo)) == str(repo.resolve())
    views = [{"session": SID, "live": True, "cwd": dest}]
    view, why = headless_mail.find(str(repo), views)
    assert view and view["session"] == SID, why
