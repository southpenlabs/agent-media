"""`GET /project/info` and `GET /project/list`: a project's page and every project
there is (project_page.py; docs/proposals/2026-11-06-project-page.md)."""

from __future__ import annotations

import subprocess

from test_org import _call, server, tree  # noqa: F401  (fixtures)


def _git(cwd, *args):
    subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True,
                   env={"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
                        "GIT_COMMITTER_EMAIL": "t@t", "PATH": "/usr/bin:/bin", "HOME": str(cwd)})


def test_a_projects_page_and_the_list(tree, server, tmp_path, monkeypatch):
    proj = tmp_path / "projects"
    repo = proj / "app"
    repo.mkdir(parents=True)
    _git(repo, "init", "-q", "-b", "main")
    (repo / "README.md").write_text("# The app\n")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-q", "-m", "the first one")
    (repo / "README.md").write_text("# The app, changed\n")
    (proj / "plain").mkdir()
    monkeypatch.setenv("MEDIA_PROJECTS_DIR", str(proj))
    monkeypatch.setenv("MEDIA_DOC_ROOTS", str(tmp_path / "nowhere"))
    monkeypatch.setenv("MEDIA_PROJECT_DIRS", "")         # not this machine's own folders
    from agent_media_server import git_state
    git_state._cache.clear()

    status, got = _call(server, "GET", "/project/info?project=p-app")
    assert status == 200 and got["root"] == str(repo) and got["project"] == "p-app"
    g = got["git"]
    assert g["branch"] == "main" and g["changed"] == 1
    assert g["last"]["subject"] == "the first one" and g["last"]["at"] > 0

    status, got = _call(server, "GET", "/project/info?path=" + str(proj / "plain"))
    assert status == 200 and "git" not in got and got["project"] == "p-plain"

    for bad in ("/project/info?project=nope", "/project/info?path=" + str(proj), "/project/info"):
        assert _call(server, "GET", bad)[0] == 404, bad
    assert _call(server, "GET", "/project/info?project=p-app", bearer="bad")[0] == 401

    status, got = _call(server, "GET", "/project/list")
    assert status == 200
    assert [(r["project"], r["folder"]) for r in got["projects"]] == [("p-app", "app"), ("p-plain", "plain")]
    assert _call(server, "GET", "/project/list", bearer="bad")[0] == 401
