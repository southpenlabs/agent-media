"""A project's page in the app, and every project there is (David, 6 Nov
2026: a page of its own for a project picked from the menu;
docs/proposals/2026-11-06-project-page.md).

`GET /project/info?project=<name>` (or `?path=<folder>`) answers the folder a
project lives in and its repo's state: branch, files changed, commits not
pushed or not pulled, and the last commit. The page's chats come from the
thread list the app already holds and its documents from `GET /docs`, so
this answers only what the app cannot know.

`GET /project/forge?project=<name>` (19 Nov 2026) answers the open issues
and pull requests of its repo's forge owner, the umbrella (forge.py).

`GET /project/list` lists every project under the roots (and the folders of
their own), for the menu's All projects.

Every row, and the page, carries `kind`: "project", or "agent" for a business
agent's folder (agent_workspaces.py), which is listed with group null and its
`name`, `persona` and `front` though it sits under no project root (David,
6 Nov 2026: the agents under an Agents heading of their own).
"""

from __future__ import annotations

import os

from agent_media_core import layout

from . import agent_workspaces, auth, doc_read, folder_grants, forge, git_state


def _last_commit(cwd: str) -> dict | None:
    out = git_state._git(cwd, "log", "-1", "--format=%h%x1f%at%x1f%s%x1f%H")
    bits = (out or "").strip("\n").split("\x1f")
    if len(bits) < 4:
        return None
    sha, at, subject, full = bits
    try:
        when = int(at)
    except ValueError:
        when = 0
    last: dict = {"sha": sha, "at": when, "subject": subject}
    # Its checks on the forge (19 Nov 2026), left out when not known.
    unpushed = git_state._unpushed(cwd)
    ci = forge.commit_ci(cwd, full, unpushed is not None and full not in unpushed)
    if ci is not forge._UNKNOWN:
        last["ci"] = ci
    return last


def git_summary(cwd: str) -> dict | None:
    """`{branch, changed, ahead, behind, last}` for a checkout, else None."""
    head = git_state.git_for(cwd)
    if head is None:
        return None
    return {**head, "last": _last_commit(cwd)}


def _name_of(root: str) -> str:
    """The name the thread list knows a project folder by."""
    idx = layout._index()
    return idx["by_path"].get(os.path.realpath(root), "")


def page(project: str, path: str, bearer: str) -> tuple[bool, dict]:
    user, err = auth.gate(bearer)
    if not user:
        return False, err
    agent = agent_workspaces.find(project, path)
    if agent and not folder_grants.may_bearer(bearer, agent["path"], "browse"):
        return False, {"error": "no such project", "status": 404}
    if agent:
        out = {"project": agent["project"], "root": agent["path"], **_agent_fields(agent)}
        git = git_summary(agent["path"])
        if git is not None:
            out["git"] = git
        return True, out
    root = doc_read.project_dir(project, path)
    if not root or not folder_grants.may_bearer(bearer, root, "browse"):
        return False, {"error": "no such project", "status": 404}
    out = {"project": project or _name_of(str(root)) or root.name, "root": str(root),
           "kind": "project"}
    git = git_summary(str(root))
    if git is not None:
        out["git"] = git
    return True, out


def forge_page(project: str, path: str, bearer: str) -> tuple[bool, dict]:
    """`GET /project/forge?project=`: the open issues and pull requests of the
    project's repo and the others of its owner (the umbrella), newest
    activity first (forge.project_view). 404 when the project is not on a
    forge with a backend."""
    user, err = auth.gate(bearer)
    if not user:
        return False, err
    agent = agent_workspaces.find(project, path)
    root = agent["path"] if agent else str(doc_read.project_dir(project, path) or "")
    if not root or not folder_grants.may_bearer(bearer, root, "browse"):
        return False, {"error": "no such project", "status": 404}
    repo = forge.repo_for(root)
    if repo is None:
        return False, {"error": "no forge for this project", "status": 404}
    return True, forge.project_view(repo)


def listing(bearer: str) -> tuple[bool, dict]:
    user, err = auth.gate(bearer)
    if not user:
        return False, err
    agents = agent_workspaces.workspaces()
    dev = auth._device(bearer)
    if folder_grants.scoped(dev):
        # Limited to its folders (folder_grants.py): those projects only.
        agents = []
        keep = lambda p: folder_grants.may(dev, p, "browse")  # noqa: E731
    else:
        keep = lambda p: True  # noqa: E731
    mine = {a["path"] for a in agents}
    rows = [{"project": name, "path": row["path"], "group": row["group"] or None,
             "folder": row["folder"], "kind": "project"}
            for name, row in layout._index()["projects"].items()
            if os.path.isdir(row["path"]) and os.path.realpath(row["path"]) not in mine
            and keep(row["path"])]
    rows.sort(key=lambda r: ((r["group"] or "").lower(), r["folder"].lower()))
    rows += [{"project": a["project"], "path": a["path"], "group": None,
              "folder": os.path.basename(a["path"]), **_agent_fields(a)} for a in agents]
    return True, {"projects": rows}


def _agent_fields(agent: dict) -> dict:
    return {"kind": "agent", "name": agent["name"], "persona": agent["persona"],
            "front": agent["front"]}
