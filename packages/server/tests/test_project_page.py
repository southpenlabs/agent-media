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
