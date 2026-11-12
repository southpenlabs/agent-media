"""git_who: which chats hold a checkout's changes (the git sheet's chips)."""

import json
import os
import subprocess
import time

import pytest

from agent_media_server import git_state, git_who, recaps


def _git(cwd, *args):
    return subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True).stdout


def _line(rec):
    return json.dumps(rec, separators=(",", ":")) + "\n"


def _use(at, cwd, tid, name, inp):
    return _line({"type": "assistant", "timestamp": at, "cwd": cwd,
                  "message": {"content": [{"type": "tool_use", "id": tid, "name": name, "input": inp}]}})


def _result(at, cwd, tid, text):
    return _line({"type": "user", "timestamp": at, "cwd": cwd,
                  "message": {"content": [{"type": "tool_result", "tool_use_id": tid, "content": text}]}})


def _iso(t):
    return time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(t))


@pytest.fixture
def repo(tmp_path, monkeypatch):
    for k in list(os.environ):
        if k.startswith(("MEDIA_", "RELAY_")):
            monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("GIT_AUTHOR_NAME", "t")
    monkeypatch.setenv("GIT_AUTHOR_EMAIL", "t@example.com")
    monkeypatch.setenv("GIT_COMMITTER_NAME", "t")
    monkeypatch.setenv("GIT_COMMITTER_EMAIL", "t@example.com")
    remote = tmp_path / "remote.git"
    _git(tmp_path, "init", "-q", "--bare", str(remote))
    r = tmp_path / "repo"
    r.mkdir()
    _git(r, "init", "-q", "-b", "main")
    (r / "a.py").write_text("one = 1\n")
    (r / "b.py").write_text("two = 2\n")
    (r / "c.py").write_text("three = 3\n")
    _git(r, "add", ".")
    old = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(time.time() - 86400))
    subprocess.run(["git", "-C", str(r), "commit", "-q", "-m", "first", "--date", old], check=True,
                   env={**os.environ, "GIT_COMMITTER_DATE": old})
    _git(r, "remote", "add", "origin", str(remote))
    _git(r, "push", "-q", "-u", "origin", "main")
    claude = tmp_path / "claude"
    monkeypatch.setattr(recaps, "_claude_dir", lambda: claude)
    git_state._cache.clear()
    git_who._seen.clear()
    return r, claude / "projects" / git_who._slug(str(r))


def test_files_and_commits_go_to_the_chats_that_made_them(repo):
    r, proj = repo
    proj.mkdir(parents=True)
    now = time.time()
    cwd = str(r)
    # Chat A: a tool edit of a.py, still in the file.
    (r / "a.py").write_text("one = 1\nadded_by_chat_a = 'hello there'\n")
    (proj / "aaaaaaaa-0000-0000-0000-000000000001.jsonl").write_text(
        _use(_iso(now - 60), cwd, "t1", "Edit", {"file_path": str(r / "a.py"), "old_string": "one = 1",
                                                  "new_string": "one = 1\nadded_by_chat_a = 'hello there'"}))
    # Chat B: a sed -i on b.py, a read of c.py, and a commit of a new file.
    (r / "b.py").write_text("two = 22\n")
    (r / "d.py").write_text("four = 4\n")
    _git(r, "add", "d.py")
    _git(r, "commit", "-q", "-m", "add d")
    sha = _git(r, "rev-parse", "--short", "HEAD").strip()
    (proj / "bbbbbbbb-0000-0000-0000-000000000002.jsonl").write_text(
        _use(_iso(now - 50), cwd, "t2", "Bash", {"command": "sed -i 's/2/22/' b.py"})
        + _use(_iso(now - 45), cwd, "t3", "Bash", {"command": "grep three c.py 2>/dev/null > /tmp/x"})
        + _use(_iso(now - 40), cwd, "t4", "Bash", {"command": "git add d.py && git commit -m 'add d'"})
        + _result(_iso(now - 39), cwd, "t4", f"[main {sha}] add d\n 1 file changed"))
    # Chat C: a tool edit of c.py that was since undone (c.py changed by hand).
    (r / "c.py").write_text("three = 33\n")
    (proj / "cccccccc-0000-0000-0000-000000000003.jsonl").write_text(
        _use(_iso(now - 30), cwd, "t5", "Edit", {"file_path": str(r / "c.py"), "old_string": "three = 3",
                                                  "new_string": "three = 'a value long enough'"}))
    got = git_who.who(cwd, now=now)
    rows = {t["session"][:8]: t for t in got["threads"]}
    assert rows["aaaaaaaa"]["files"] == [{"path": "a.py", "how": "edit"}]
    assert rows["bbbbbbbb"]["files"] == [{"path": "b.py", "how": "shell"}]
    assert rows["bbbbbbbb"]["commits"] == [_git(r, "rev-parse", "HEAD").strip()[:12]]
    assert "cccccccc" not in rows
    assert got["unclaimed"] == {"files": ["c.py"], "commits": []}
    assert [t["session"][:8] for t in got["threads"]] == ["bbbbbbbb", "aaaaaaaa"]


def test_reads_on_from_where_it_stopped(repo):
    r, proj = repo
    proj.mkdir(parents=True)
    now = time.time()
    t = proj / "aaaaaaaa-0000-0000-0000-000000000001.jsonl"
    t.write_text(_use(_iso(now - 60), str(r), "t1", "Bash", {"command": "echo hi"}))
    assert git_who.who(str(r), now=now)["threads"] == []
    (r / "b.py").write_text("two = 2\nmore_from_a_heredoc = True\n")
    with open(t, "a") as fh:
        fh.write(_use(_iso(now - 10), str(r), "t2", "Bash",
                      {"command": "python3 - <<'EOF'\nopen('b.py','a').write('more_from_a_heredoc = True\\n')\nEOF"}))
    git_state._cache.clear()
    got = git_who.who(str(r), now=now)
    assert got["threads"][0]["files"] == [{"path": "b.py", "how": "shell"}]


def test_a_heredoc_whose_lines_are_gone_is_not_counted(repo):
    r, proj = repo
    proj.mkdir(parents=True)
    now = time.time()
    (r / "b.py").write_text("two = 'something else entirely'\n")
    (proj / "aaaaaaaa-0000-0000-0000-000000000001.jsonl").write_text(
        _use(_iso(now - 60), str(r), "t1", "Bash",
             {"command": "python3 - <<'EOF'\nopen('b.py','w').write('two = \"what it wrote back then\"\\n')\nEOF"}))
    got = git_who.who(str(r), now=now)
    assert got["threads"] == [] and got["unclaimed"]["files"] == ["b.py"]


def test_outside_a_checkout(tmp_path):
    assert git_who.who(str(tmp_path)) is None


def test_add_changes_marks_the_rows_from_a_snapshot(repo, monkeypatch):
    """The list's `changes`: counted, from the snapshot, refreshed behind it."""
    r, proj = repo
    proj.mkdir(parents=True)
    now = time.time()
    cwd = str(r)
    (r / "a.py").write_text("one = 1\nadded_by_chat_a = 'hello there'\n")
    (proj / "aaaaaaaa-0000-0000-0000-000000000001.jsonl").write_text(
        _use(_iso(now - 60), cwd, "t1", "Edit", {"file_path": str(r / "a.py"), "old_string": "one = 1",
                                                 "new_string": "one = 1\nadded_by_chat_a = 'hello there'"}))
    git_who._snapshots.clear()
    git_who._tops.clear()
    done = []
    monkeypatch.setattr(git_who, "_read_soon", lambda cwd, top: done.append((cwd, top)))
    rows = [{"session": "aaaaaaaa-0000-0000-0000-000000000001", "cwd": cwd},
            {"session": "bbbbbbbb-0000-0000-0000-000000000002", "cwd": cwd},
            {"session": "cccccccc-0000-0000-0000-000000000003", "cwd": None}]
    # Nothing looked up yet: no mark, no git in the answer's own time, and the
    # folder queued to be read behind it.
    git_who.add_changes(rows, now=now)
    assert [row["changes"] for row in rows] == [None, None, None]
    assert done == [(cwd, None)]
    # Once read, the chat that wrote the file carries its count; the others none.
    assert git_who.top_of(cwd) == str(r)
    git_who.refresh(str(r))
    git_who.add_changes(rows, now=now)
    assert rows[0]["changes"]["files"] == 1 and rows[0]["changes"]["commits"] == 0
    assert rows[1]["changes"] is None and rows[2]["changes"] is None
    # A fresh snapshot is not read again; a stale one is.
    assert done == [(cwd, None)]
    git_who.add_changes(rows, now=now + git_who.SNAPSHOT_S + 1)
    assert done == [(cwd, None), ("", str(r))]


def test_add_changes_counts_a_chats_unpushed_commits(repo):
    r, proj = repo
    proj.mkdir(parents=True)
    now = time.time()
    cwd = str(r)
    (r / "d.py").write_text("four = 4\n")
    _git(r, "add", "d.py")
    _git(r, "commit", "-q", "-m", "add d")
    sha = _git(r, "rev-parse", "--short", "HEAD").strip()
    (proj / "bbbbbbbb-0000-0000-0000-000000000002.jsonl").write_text(
        _use(_iso(now - 40), cwd, "t4", "Bash", {"command": "git add d.py && git commit -m 'add d'"})
        + _result(_iso(now - 39), cwd, "t4", f"[main {sha}] add d\n 1 file changed"))
    git_who._snapshots.clear()
    git_who._tops.clear()
    assert git_who.top_of(cwd) == cwd
    held = git_who.refresh(cwd)
    rows = [{"session": "bbbbbbbb-0000-0000-0000-000000000002", "cwd": cwd}]
    git_who.add_changes(rows, now=now)
    assert held["bbbbbbbb-0000-0000-0000-000000000002"]["commits"] == 1
    assert rows[0]["changes"] == {"files": 0, "commits": 1, "at": held["bbbbbbbb-0000-0000-0000-000000000002"]["at"]}


def test_add_changes_outside_a_checkout(tmp_path):
    git_who._tops.clear()
    assert git_who.top_of(str(tmp_path)) is None
    rows = [{"session": "aaaaaaaa-0000-0000-0000-000000000001", "cwd": str(tmp_path)}]
    assert git_who.add_changes(rows)[0]["changes"] is None
