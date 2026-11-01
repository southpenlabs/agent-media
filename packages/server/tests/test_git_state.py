"""git_state: a thread folder's branch and changed files (1 Nov 2026)."""

import subprocess

import pytest

from agent_media_server import git_state


def test_parse_branch_ahead_behind_and_changes():
    out = "## main...origin/main [ahead 2, behind 1]\n M a.py\n?? b.txt\nA  c.md\n"
    assert git_state.parse(out) == {"branch": "main", "changed": 3, "ahead": 2, "behind": 1}


def test_parse_clean_detached_and_unborn():
    assert git_state.parse("## topic\n") == {"branch": "topic", "changed": 0, "ahead": 0, "behind": 0}
    assert git_state.parse("## HEAD (no branch)\n M x\n")["branch"] is None
    assert git_state.parse("## No commits yet on main\n")["branch"] == "main"


@pytest.fixture
def repo(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", "/dev/null")
    git_state._cache.clear()
    r = tmp_path / "r"
    r.mkdir()
    run = lambda *a: subprocess.run(["git", "-C", str(r), *a], check=True, capture_output=True)
    run("init", "-q", "-b", "work")
    run("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "start")
    return r


def test_git_for_a_checkout_and_its_cache(repo):
    assert git_state.git_for(str(repo), now=0.0) == {"branch": "work", "changed": 0, "ahead": 0, "behind": 0}
    (repo / "new.txt").write_text("x")
    # Within CACHE_S the answer is kept; after it, read again.
    assert git_state.git_for(str(repo), now=1.0)["changed"] == 0
    assert git_state.git_for(str(repo), now=1.0 + git_state.CACHE_S)["changed"] == 1


def test_git_for_outside_a_checkout(tmp_path, monkeypatch):
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path))
    git_state._cache.clear()
    assert git_state.git_for(str(tmp_path), now=0.0) is None
    assert git_state.git_for("", now=0.0) is None
    assert git_state.git_for(str(tmp_path / "missing"), now=0.0) is None
