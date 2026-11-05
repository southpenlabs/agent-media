"""A project's page in the app, and every project there is (David, 6 Nov
2026: a page of its own for a project picked from the menu;
docs/proposals/2026-11-06-project-page.md).

`GET /project?project=<name>` (or `?path=<folder>`) answers the folder a
project lives in and its repo's state: branch, files changed, commits not
pushed or not pulled, and the last commit. The page's chats come from the
thread list the app already holds and its documents from `GET /docs`, so
this answers only what the app cannot know.

`GET /projects` lists every project under the roots (and the folders of
their own), for the menu's All projects.
"""

from __future__ import annotations

import os

from agent_media_core import layout

from . import auth, doc_read, git_state


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
    root = doc_read.project_dir(project, path)
    if not root:
        return False, {"error": "no such project", "status": 404}
    out = {"project": project or _name_of(str(root)) or root.name, "root": str(root)}
    git = git_summary(str(root))
    if git is not None:
        out["git"] = git
    return True, out


def listing(bearer: str) -> tuple[bool, dict]:
    user, err = auth.gate(bearer)
    if not user:
        return False, err
    rows = [{"project": name, "path": row["path"], "group": row["group"] or None,
             "folder": row["folder"]}
            for name, row in layout._index()["projects"].items()
            if os.path.isdir(row["path"])]
    rows.sort(key=lambda r: ((r["group"] or "").lower(), r["folder"].lower()))
    return True, {"projects": rows}
