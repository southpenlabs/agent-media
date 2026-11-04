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


def _commit(repo, *files):
    run = lambda *a: subprocess.run(["git", "-C", str(repo), *a], check=True, capture_output=True)
    run("add", *files)
    run("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "add")


def test_changes_lists_files_with_counts(repo):
    (repo / "a.txt").write_text("one\ntwo\n")
    _commit(repo, "a.txt")
    (repo / "a.txt").write_text("one\nTWO\nthree\n")
    (repo / "new.txt").write_text("x\n")
    got = git_state.changes(str(repo))
    by = {f["path"]: f for f in got["files"]}
    assert by["a.txt"] == {"path": "a.txt", "status": "M", "added": 2, "removed": 1}
    assert by["new.txt"]["untracked"] is True
    assert got["branch"] == "work" and got["commits"] == [] and got["more"] == 0


def test_file_diff_only_for_listed_files(repo):
    (repo / "a.txt").write_text("one\n")
    (repo / "kept.txt").write_text("secret\n")
    _commit(repo, "a.txt", "kept.txt")
    (repo / "a.txt").write_text("two\n")
    (repo / "new.txt").write_text("fresh\n")
    d = git_state.file_diff(str(repo), "a.txt")
    assert "-one" in d["diff"] and "+two" in d["diff"] and not d["truncated"]
    assert "+fresh" in git_state.file_diff(str(repo), "new.txt")["diff"]
    # Unchanged, outside the checkout, or made up: nothing.
    assert git_state.file_diff(str(repo), "kept.txt") is None
    assert git_state.file_diff(str(repo), "../etc/passwd") is None
    assert git_state.file_diff(str(repo), "nope.txt") is None


def test_changes_outside_a_checkout(tmp_path, monkeypatch):
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path))
    git_state._cache.clear()
    assert git_state.changes(str(tmp_path)) is None
    assert git_state.file_diff(str(tmp_path), "x") is None


@pytest.mark.parametrize("url,web,kind", [
    ("git@github.com:a/b.git", "https://github.com/a/b", "github"),
    ("https://user:tok@github.com/a/b.git", "https://github.com/a/b", "github"),
    ("ssh://git@gitlab.com:2222/g/sub/r.git", "https://gitlab.com/g/sub/r", "gitlab"),
    ("https://codeberg.org/a/b", "https://codeberg.org/a/b", "forgejo"),
    ("git@bitbucket.org:a/b.git", "https://bitbucket.org/a/b", "bitbucket"),
    ("https://git.sr.ht/~a/b", "https://git.sr.ht/~a/b", "sourcehut"),
])
def test_web_of_known_forges(url, web, kind):
    got = git_state.web_of(url)
    assert got["web"] == web and got["kind"] == kind


def test_web_of_a_self_hosted_forge_needs_naming(monkeypatch):
    assert git_state.web_of("git@git.example.org:me/r.git") is None
    monkeypatch.setenv("MEDIA_GIT_FORGES", "git.example.org=forgejo, code.example.com=gitlab")
    r = git_state.web_of("https://git.example.org:3000/me/r.git")
    assert r == {"kind": "forgejo", "host": "git.example.org", "web": "https://git.example.org:3000/me/r"}
    assert git_state.web_of("git@code.example.com:t/r")["kind"] == "gitlab"


def test_links_per_forge():
    gh = {"kind": "github", "web": "https://github.com/a/b"}
    gl = {"kind": "gitlab", "web": "https://gitlab.com/a/b"}
    fj = {"kind": "forgejo", "web": "https://codeberg.org/a/b"}
    bb = {"kind": "bitbucket", "web": "https://bitbucket.org/a/b"}
    sh = {"kind": "sourcehut", "web": "https://git.sr.ht/~a/b"}
    assert git_state.commit_url(gh, "abc") == "https://github.com/a/b/commit/abc"
    assert git_state.commit_url(gl, "abc") == "https://gitlab.com/a/b/-/commit/abc"
    assert git_state.commit_url(bb, "abc") == "https://bitbucket.org/a/b/commits/abc"
    assert git_state.branch_url(fj, "topic") == "https://codeberg.org/a/b/src/branch/topic"
    assert git_state.compare_url(gh, "main", "topic") == "https://github.com/a/b/compare/main...topic"
    assert git_state.compare_url(gl, "main", "topic") == "https://gitlab.com/a/b/-/compare/main...topic"
    assert git_state.compare_url(fj, "main", "topic") == "https://codeberg.org/a/b/compare/main...topic"
    assert git_state.compare_url(bb, "main", "topic") == "https://bitbucket.org/a/b/branches/compare/topic%0Dmain"
    assert git_state.compare_url(sh, "main", "topic") is None
    assert git_state.compare_url(gh, "main", "main") is None


def test_history_commit_and_commit_diff(repo):
    (repo / "a.txt").write_text("one\n")
    _commit(repo, "a.txt")
    (repo / "a.txt").write_text("two\n")
    _commit(repo, "a.txt")
    got = git_state.changes(str(repo))
    h = got["history"]
    # No remote: nothing is "not pushed", and nothing links.
    assert len(h) == 3 and h[0]["pushed"] is None and h[0]["url"] is None and got["commits"] == []
    assert got["remote"] is None and got["links"] == {"branch": None, "compare": None}
    c = git_state.commit(str(repo), h[0]["sha"])
    assert c["files"] == [{"path": "a.txt", "status": "M", "added": 1, "removed": 1}]
    d = git_state.commit_diff(str(repo), h[0]["sha"], "a.txt")
    assert "-one" in d["diff"] and "+two" in d["diff"]
    # The first commit: all new against the empty tree.
    assert git_state.commit(str(repo), h[-1]["sha"])["files"] == []
    assert git_state.commit(str(repo), h[1]["sha"])["files"][0]["status"] == "A"


def test_commit_only_on_the_branch(repo):
    run = lambda *a: subprocess.run(["git", "-C", str(repo), *a], check=True, capture_output=True)
    run("checkout", "-q", "-b", "side")
    (repo / "s.txt").write_text("s\n")
    _commit(repo, "s.txt")
    side = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    run("checkout", "-q", "work")
    assert git_state.commit(str(repo), side) is None
    assert git_state.commit(str(repo), "HEAD") is None  # names are not shas
    assert git_state.commit(str(repo), "zz") is None
    assert git_state.commit_diff(str(repo), side, "s.txt") is None


def test_pushed_and_not_against_a_remote(repo, tmp_path):
    run = lambda *a: subprocess.run(["git", "-C", str(repo), *a], check=True, capture_output=True)
    subprocess.run(["git", "init", "-q", "--bare", str(tmp_path / "up.git")], check=True)
    run("remote", "add", "origin", str(tmp_path / "up.git"))
    run("push", "-q", "-u", "origin", "work")
    (repo / "late.txt").write_text("x\n")
    _commit(repo, "late.txt")
    git_state._cache.clear()
    got = git_state.changes(str(repo))
    assert [c["pushed"] for c in got["history"]] == [False, True]
    assert got["ahead"] == 1 and [c["subject"] for c in got["commits"]] == ["add"]
    # A plain path is no forge: no links.
    assert got["remote"] is None and got["history"][1]["url"] is None


def test_tree_lists_one_folder_git_shows(repo):
    (repo / "src" / "deep").mkdir(parents=True)
    (repo / "src" / "a.py").write_text("print(1)\n")
    (repo / "src" / "deep" / "b.py").write_text("x\n")
    (repo / "README.md").write_text("hi\n")
    (repo / ".gitignore").write_text(".env\nbuild/\n")
    _commit(repo, "src", "README.md", ".gitignore")
    (repo / ".env").write_text("TOKEN=secret\n")
    (repo / "build").mkdir()
    (repo / "build" / "out.js").write_text("x\n")
    (repo / "src" / "a.py").write_text("print(2)\n")
    (repo / "notes").mkdir()
    (repo / "notes" / "n.txt").write_text("new\n")
    got = git_state.tree(str(repo), "")
    names = [(e["name"], e.get("folder", False)) for e in got["entries"]]
    # Folders first, then files; ignored ones never appear.
    assert names == [("notes", True), ("src", True), (".gitignore", False), ("README.md", False)]
    by = {e["name"]: e for e in got["entries"]}
    assert by["src"]["changed"] == 1 and by["notes"]["changed"] == 1
    assert by["README.md"] == {"name": "README.md", "path": "README.md", "size": 3}
    sub = git_state.tree(str(repo), "src/")
    assert sub["tree"] == "src"
    assert [e["path"] for e in sub["entries"]] == ["src/deep", "src/a.py"]
    assert sub["entries"][1]["status"] == "M"
    assert git_state.tree(str(repo), "notes")["entries"][0]["status"] == "??"
    # Not listed, outside, or into .git: nothing.
    for bad in ("build", "../", "src/../..", ".git", "missing"):
        assert git_state.tree(str(repo), bad) is None, bad


def test_blob_reads_only_files_git_lists(repo):
    (repo / "a.txt").write_text("one\ntwo\n")
    (repo / "bin.dat").write_bytes(b"\x00\x01\x02")
    (repo / ".gitignore").write_text(".env\n")
    _commit(repo, "a.txt", "bin.dat", ".gitignore")
    (repo / ".env").write_text("TOKEN=secret\n")
    (repo / "loose").mkdir()
    (repo / "loose" / "x.md").write_text("# x\n")
    got = git_state.blob(str(repo), "a.txt")
    assert got["text"] == "one\ntwo\n" and got["status"] is None and not got["binary"]
    assert got["size"] == 8 and got["url"] is None
    assert git_state.blob(str(repo), "bin.dat")["binary"] is True
    assert git_state.blob(str(repo), "loose/x.md")["status"] == "??"
    for bad in (".env", "../r/.env", ".git/config", "loose", "", "nope.txt"):
        assert git_state.blob(str(repo), bad) is None, bad


def test_blob_cut_at_its_limit(repo, monkeypatch):
    monkeypatch.setattr(git_state, "MAX_TEXT", 10)
    (repo / "long.txt").write_text("x" * 50)
    got = git_state.blob(str(repo), "long.txt")
    assert got["text"] == "x" * 10 and got["truncated"] is True and got["size"] == 50


def test_blob_and_tree_links_per_forge():
    gh = {"kind": "github", "host": "github.com", "web": "https://github.com/o/r"}
    assert git_state.blob_url(gh, "abc", "src/a.py") == "https://github.com/o/r/blob/abc/src/a.py"
    assert git_state.blob_url(gh, "abc", "src", folder=True) == "https://github.com/o/r/tree/abc/src"
    assert git_state.blob_url(gh, "abc", "", folder=True) == "https://github.com/o/r/tree/abc"
    fj = {"kind": "forgejo", "host": "codeberg.org", "web": "https://codeberg.org/o/r"}
    assert git_state.blob_url(fj, "abc", "a.py") == "https://codeberg.org/o/r/src/commit/abc/a.py"
    gl = {"kind": "gitlab", "host": "gitlab.com", "web": "https://gitlab.com/o/r"}
    assert git_state.blob_url(gl, "abc", "a.py") == "https://gitlab.com/o/r/-/blob/abc/a.py"
    sh = {"kind": "sourcehut", "host": "git.sr.ht", "web": "https://git.sr.ht/~o/r"}
    assert git_state.blob_url(sh, "abc", "a.py") == "https://git.sr.ht/~o/r/tree/abc/item/a.py"
    assert git_state.blob_url(None, "abc", "a.py") is None
    assert git_state.blob_url(gh, "", "a.py") is None


def test_git_changes_routes_tree_and_blob(repo, monkeypatch):
    from agent_media_server import auth, sessions, threads
    monkeypatch.setattr(auth, "gate", lambda bearer: ("david", None))
    monkeypatch.setattr(sessions, "latest_cwd", lambda s: str(repo))
    (repo / "a.txt").write_text("hi\n")
    sid = "0" * 8 + "-0000-0000-0000-" + "0" * 12
    ok, got = threads.git_changes(sid, "t", tree="")
    assert ok and [e["name"] for e in got["entries"]] == ["a.txt"]
    ok, got = threads.git_changes(sid, "t", blob="a.txt")
    assert ok and got["text"] == "hi\n"
    ok, got = threads.git_changes(sid, "t", blob=".git/HEAD")
    assert not ok and got["status"] == 404
    ok, got = threads.git_changes(sid, "t", tree="nowhere")
    assert not ok and got["status"] == 404
    # Neither: the changes, as before.
    ok, got = threads.git_changes(sid, "t")
    assert ok and "files" in got
