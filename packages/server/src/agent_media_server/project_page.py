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
import re
import threading
import time

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
    # Another checkout of a listed project (19 Nov 2026): its main one, how,
    # and its branch; only when that main is listed here too.
    held = checkouts()
    shown = {r["project"] for r in rows}
    for r in rows:
        c = held.get(r["project"])
        if c and c["of"] in shown:
            r["checkout_of"], r["checkout"], r["branch"] = c["of"], c["how"], c["branch"]
    rows.sort(key=lambda r: ((r["group"] or "").lower(), r["folder"].lower()))
    rows += [{"project": a["project"], "path": a["path"], "group": None,
              "folder": os.path.basename(a["path"]), **_agent_fields(a)} for a in agents]
    return True, {"projects": rows}


def _agent_fields(agent: dict) -> dict:
    return {"kind": "agent", "name": agent["name"], "persona": agent["persona"],
            "front": agent["front"]}


# ── Checkouts: a project that is another checkout of one listed ────────────
#
# David, 19 Nov 2026: "Do the worktree grouping next" (agent mail #1067 from
# sasonica-app; docs/proposals/2026-11-19-forgejo-workflow.md §3). Twenty-odd
# sasonica-app worktree folders were peers of it in every list. A worktree's
# `.git` is a file naming a gitdir under <main>/.git/worktrees/, and its main
# is the folder holding that .git; a clone shares its origin with another
# listed project, and the main of a set of clones is the one whose folder is
# named as the repo is on the remote, else the oldest. Only the projects the
# index lists (a worktree inside a repo, .worktrees/x, is not one). Chains
# are left for the app to follow (`of` names the next one up).

CHECKOUTS_S = 60.0
_checkouts_lock = threading.Lock()
_checkouts: tuple[float, dict[str, dict]] | None = None


def _origin_key(url: str) -> str:
    """An origin URL as host/owner/repo, lower case, whatever its scheme."""
    url = (url or "").strip()
    m = re.fullmatch(r"(?:[a-z+]+://)?(?:[^@/]+@)?([^/:]+)(?::\d+)?[:/](.+?)(?:\.git)?/?", url, re.I)
    if not m:
        return url.lower()
    return f"{m.group(1)}/{m.group(2)}".lower()


def _worktree_main(path: str) -> str | None:
    """The main checkout's folder when `path` is a linked worktree."""
    dot = os.path.join(path, ".git")
    if not os.path.isfile(dot):
        return None
    try:
        with open(dot, encoding="utf-8") as f:
            line = f.readline().strip()
    except OSError:
        return None
    if not line.startswith("gitdir:"):
        return None
    gitdir = line.partition(":")[2].strip()
    if not os.path.isabs(gitdir):
        gitdir = os.path.join(path, gitdir)
    head, sep, _ = os.path.realpath(gitdir).partition(f"{os.sep}.git{os.sep}worktrees{os.sep}")
    return head if sep else None


def _born(path: str) -> float:
    """When the checkout was made, near enough: .git/description is written
    by clone and init and seldom touched after."""
    for leaf in ("description", "config", ""):
        try:
            return os.stat(os.path.join(path, ".git", leaf)).st_mtime
        except OSError:
            continue
    return float("inf")


def _branch(path: str) -> str | None:
    out = (git_state._git(path, "symbolic-ref", "--short", "-q", "HEAD", ok=(0, 1)) or "").strip()
    return out or None


def _find_checkouts() -> dict[str, dict]:
    by_real: dict[str, str] = {}
    for name, row in layout._index()["projects"].items():
        if os.path.isdir(row["path"]):
            by_real.setdefault(os.path.realpath(row["path"]), name)
    out: dict[str, dict] = {}
    origins: dict[str, list[tuple[str, str]]] = {}
    for real, name in by_real.items():
        main = _worktree_main(real)
        if main is not None:
            of = by_real.get(main)
            if of and of != name:
                out[name] = {"of": of, "how": "worktree", "branch": _branch(real)}
            continue
        if not os.path.isdir(os.path.join(real, ".git")):
            continue
        url = (git_state._git(real, "config", "--get", "remote.origin.url", ok=(0, 1)) or "").strip()
        if url:
            origins.setdefault(_origin_key(url), []).append((real, name))
    for key, group in origins.items():
        if len(group) < 2:
            continue
        repo = key.rsplit("/", 1)[-1]
        named = [g for g in group if os.path.basename(g[0]).lower() == repo]
        main_real, main_name = min(named or group, key=lambda g: (_born(g[0]), g[0]))
        for real, name in group:
            if name != main_name:
                out[name] = {"of": main_name, "how": "clone", "branch": _branch(real)}
    return out


def checkouts() -> dict[str, dict]:
    """`{project: {"of", "how": "worktree" | "clone", "branch"}}` for every
    listed project that is another checkout of one listed; kept a minute."""
    global _checkouts
    with _checkouts_lock:
        now = time.monotonic()
        if _checkouts is not None and now - _checkouts[0] < CHECKOUTS_S:
            return _checkouts[1]
        found = _find_checkouts()
        _checkouts = (now, found)
        return found
