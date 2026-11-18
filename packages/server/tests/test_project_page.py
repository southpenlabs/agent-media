"""`GET /project/info` and `GET /project/list`: a project's page and every project
there is (project_page.py; docs/proposals/2026-11-06-project-page.md)."""

from __future__ import annotations

import subprocess

from agent_media_server import forge

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
    assert got["kind"] == "project"
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
    assert [(r["project"], r["folder"], r["kind"]) for r in got["projects"]] == \
        [("p-app", "app", "project"), ("p-plain", "plain", "project")]
    assert _call(server, "GET", "/project/list", bearer="bad")[0] == 401


def _agents(tmp_path, monkeypatch):
    """Three folders under a tmp agents folder (two with CLAUDE.md), and a
    personas repo naming nova (with a display name) and vic (without)."""
    agents = tmp_path / "spl" / "agents"
    for name in ("nova", "vic", "notes"):
        (agents / name).mkdir(parents=True)
    (agents / "nova" / "CLAUDE.md").write_text("# Nova\n")
    (agents / "vic" / "CLAUDE.md").write_text("# Vic\n")
    people = tmp_path / "agent-personas" / "personas"
    people.mkdir(parents=True)
    (people / "nova.md").write_text("---\nid: nova\nname: Nova the Front\nkind: persona\n---\nbody\n")
    monkeypatch.setenv("MEDIA_AGENT_WORKSPACES", str(agents))
    monkeypatch.setenv("MEDIA_PERSONAS_DIR", str(tmp_path / "agent-personas"))
    return agents


def test_agent_rows_on_the_project_list(tree, server, tmp_path, monkeypatch):
    agents = _agents(tmp_path, monkeypatch)
    proj = tmp_path / "projects"
    (proj / "plain").mkdir(parents=True)
    monkeypatch.setenv("MEDIA_PROJECTS_DIR", str(proj))
    monkeypatch.setenv("MEDIA_DOC_ROOTS", str(tmp_path / "nowhere"))
    monkeypatch.setenv("MEDIA_PROJECT_DIRS", "")

    status, got = _call(server, "GET", "/project/list")
    assert status == 200
    rows = got["projects"]
    assert all(r["kind"] in ("project", "agent") for r in rows)
    assert [r for r in rows if r["kind"] == "agent"] == [
        {"project": "nova", "path": str(agents / "nova"), "group": None, "folder": "nova",
         "kind": "agent", "name": "Nova the Front", "persona": "nova", "front": True},
        {"project": "vic", "path": str(agents / "vic"), "group": None, "folder": "vic",
         "kind": "agent", "name": "Vic", "persona": None, "front": False}]
    assert [r["project"] for r in rows if r["kind"] == "project"] == ["p-plain"]

    # An agent's page opens though its folder is under no project root.
    status, page = _call(server, "GET", "/project/info?path=" + str(agents / "vic"))
    assert status == 200 and page["kind"] == "agent" and page["root"] == str(agents / "vic")
    assert page["project"] == "vic" and page["persona"] is None
    status, page = _call(server, "GET", "/project/info?project=nova")
    assert status == 200 and page["kind"] == "agent" and page["front"] is True

    monkeypatch.setenv("MEDIA_AGENT_WORKSPACES", str(tmp_path / "missing"))
    rows = _call(server, "GET", "/project/list")[1]["projects"]
    assert [r["kind"] for r in rows] == ["project"]


def test_project_forge_route(tree, server, tmp_path, monkeypatch):
    proj = tmp_path / "projects"
    r = proj / "demo"
    r.mkdir(parents=True)
    _git(r, "init", "-q", "-b", "main")
    (proj / "plain").mkdir()
    monkeypatch.setenv("MEDIA_PROJECTS_DIR", str(proj))
    monkeypatch.setenv("MEDIA_DOC_ROOTS", str(tmp_path / "nowhere"))
    monkeypatch.setenv("MEDIA_PROJECT_DIRS", "")
    monkeypatch.setenv("MEDIA_GIT_FORGES", "forge.example.com=forgejo")
    monkeypatch.setattr(forge, "_open", lambda *a: (404, None))   # a forge with nothing to say
    forge.forget()
    assert _call(server, "GET", "/project/forge?project=p-demo")[0] == 404      # no remote yet
    _git(r, "remote", "add", "origin", "https://forge.example.com/sasonica/demo.git")
    forge.forget()
    status, got = _call(server, "GET", "/project/forge?project=p-demo")
    assert status == 200 and got["repo"]["name"] == "sasonica/demo" and got["items"] == []
    assert got["umbrella"] is None
    assert _call(server, "GET", "/project/forge?project=p-plain")[0] == 404
    assert _call(server, "GET", "/project/forge?project=p-demo", bearer="bad")[0] == 401


def test_other_checkouts_fold_under_their_main_one(tree, server, tmp_path, monkeypatch):
    """A worktree names its main checkout, a clone the one named as its repo
    (agent mail #1067; forgejo-workflow.md §3); a chain is left to follow."""
    import os

    from agent_media_server import git_state, project_page

    proj = tmp_path / "projects"
    app = proj / "app"
    app.mkdir(parents=True)
    _git(app, "init", "-q", "-b", "main")
    (app / "a").write_text("a\n")
    _git(app, "add", "a")
    _git(app, "commit", "-q", "-m", "one")
    _git(app, "remote", "add", "origin", "git@forge.example:me/app.git")
    _git(proj, "clone", "-q", str(app), str(proj / "app-copy"))
    copy = proj / "app-copy"
    _git(copy, "remote", "set-url", "origin", "https://forge.example/me/app")
    # the copy is younger than app whatever the clock says; app is named as the repo anyway
    os.utime(app / ".git" / "description", (1, 1))
    _git(copy, "worktree", "add", "-q", "-b", "feature", str(proj / "app-feature"))
    _git(app, "worktree", "add", "-q", "--detach", str(proj / "app-look"))
    inner = app / ".worktrees" / "x"
    _git(app, "worktree", "add", "-q", "-b", "inner", str(inner))
    other = proj / "other"
    other.mkdir()
    _git(other, "init", "-q", "-b", "main")
    monkeypatch.setenv("MEDIA_PROJECTS_DIR", str(proj))
    monkeypatch.setenv("MEDIA_DOC_ROOTS", str(tmp_path / "nowhere"))
    monkeypatch.setenv("MEDIA_PROJECT_DIRS", "")
    git_state._cache.clear()
    monkeypatch.setattr(project_page, "_checkouts", None)

    assert project_page.checkouts() == {
        "p-app-copy": {"of": "p-app", "how": "clone", "branch": "main"},
        "p-app-feature": {"of": "p-app-copy", "how": "worktree", "branch": "feature"},
        "p-app-look": {"of": "p-app", "how": "worktree", "branch": None},
    }
    status, got = _call(server, "GET", "/project/list")
    assert status == 200
    rows = {r["project"]: r for r in got["projects"]}
    assert rows["p-app-feature"]["checkout_of"] == "p-app-copy"
    assert rows["p-app-feature"]["checkout"] == "worktree" and rows["p-app-feature"]["branch"] == "feature"
    assert "checkout_of" not in rows["p-app"] and "checkout_of" not in rows["p-other"]


def test_origin_keys_match_across_schemes():
    from agent_media_server.project_page import _origin_key

    keys = {_origin_key(u) for u in ("git@github.com:Me/App.git", "https://github.com/me/app",
                                     "ssh://git@github.com:22/me/app.git", "https://github.com/me/app.git/")}
    assert keys == {"github.com/me/app"}
