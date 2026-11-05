"""A project's page in the app, and every project there is (David, 6 Nov
2026: a page of its own for a project picked from the menu;
docs/proposals/2026-11-06-project-page.md).

`GET /project/info?project=<name>` (or `?path=<folder>`) answers the folder a
project lives in and its repo's state: branch, files changed, commits not
pushed or not pulled, and the last commit. The page's chats come from the
thread list the app already holds and its documents from `GET /docs`, so
this answers only what the app cannot know.

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

from . import agent_workspaces, auth, doc_read, git_state


def _last_commit(cwd: str) -> dict | None:
    out = git_state._git(cwd, "log", "-1", "--format=%h%x1f%at%x1f%s")
    bits = (out or "").strip("\n").split("\x1f")
    if len(bits) < 3:
        return None
    sha, at, subject = bits
    try:
        when = int(at)
    except ValueError:
        when = 0
    return {"sha": sha, "at": when, "subject": subject}


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
    if agent:
        out = {"project": agent["project"], "root": agent["path"], **_agent_fields(agent)}
        git = git_summary(agent["path"])
        if git is not None:
            out["git"] = git
        return True, out
    root = doc_read.project_dir(project, path)
    if not root:
        return False, {"error": "no such project", "status": 404}
    out = {"project": project or _name_of(str(root)) or root.name, "root": str(root),
           "kind": "project"}
    git = git_summary(str(root))
    if git is not None:
        out["git"] = git
    return True, out


def listing(bearer: str) -> tuple[bool, dict]:
    user, err = auth.gate(bearer)
    if not user:
        return False, err
    agents = agent_workspaces.workspaces()
    mine = {a["path"] for a in agents}
    rows = [{"project": name, "path": row["path"], "group": row["group"] or None,
             "folder": row["folder"], "kind": "project"}
            for name, row in layout._index()["projects"].items()
            if os.path.isdir(row["path"]) and os.path.realpath(row["path"]) not in mine]
    rows.sort(key=lambda r: ((r["group"] or "").lower(), r["folder"].lower()))
    rows += [{"project": a["project"], "path": a["path"], "group": None,
              "folder": os.path.basename(a["path"]), **_agent_fields(a)} for a in agents]
    return True, {"projects": rows}


def _agent_fields(agent: dict) -> dict:
    return {"kind": "agent", "name": agent["name"], "persona": agent["persona"],
            "front": agent["front"]}
