"""A git worktree per chat in a shared repo (docs/proposals/
2026-11-17-worktree-per-chat.md).

David, 17 Nov 2026 (sasonica-app thread d69cd7ac, agent mail #860): each
chat in a shared repo works in its own checkout, so a commit is no longer
carved out of other sessions' hunks and a build is not rebuilt under a test
run.

`MEDIA_CHAT_WORKTREES` lists the repo tops (os.pathsep) that opt in, on this
host. A fresh chat started at one of them is moved to
`<repo>/.worktrees/chat-<id8>` on branch `chat/<id8>`, cut from the repo's
`main` as the shared checkout has it, with `node_modules` and the paths in
`MEDIA_CHAT_WORKTREE_LINKS` linked from the main checkout. Claude Code's own
`--worktree` does the same for Claude chats alone; sessiond runs other
harnesses too, so it is done here (the proposal says what else was looked
at). Anything that goes wrong leaves the chat where it was asked to start.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from . import new_project

log = logging.getLogger(__name__)

DIR = ".worktrees"
BRANCH_PREFIX = "chat/"
LINKS = ("node_modules",)


def _paths(name: str) -> list[str]:
    raw = os.environ.get(name) or ""
    return [p.strip() for p in raw.split(os.pathsep) if p.strip()]


def _real(path: str) -> str:
    return os.path.realpath(os.path.expanduser(path)).rstrip("/") or "/"


def repos() -> list[str]:
    return [_real(p) for p in _paths("MEDIA_CHAT_WORKTREES")]


def opted_in(cwd: str) -> bool:
    """`cwd` is the top of a repo that opts in (not a folder inside one: a
    chat started there asked for that folder)."""
    return bool(cwd) and _real(cwd) in repos()


def links() -> list[str]:
    out = list(LINKS)
    for rel in _paths("MEDIA_CHAT_WORKTREE_LINKS"):
        rel = rel.strip("/")
        if rel and ".." not in Path(rel).parts and rel not in out:
            out.append(rel)
    return out


def owner(cwd: str) -> str:
    """The checkout a chat worktree belongs to (`<repo>` for
    `<repo>/.worktrees/<name>[/…]`), else `cwd` itself."""
    real = _real(cwd) if cwd else ""
    parts = Path(real).parts
    if DIR in parts:
        i = parts.index(DIR)
        if i > 0 and len(parts) > i + 1:
            return str(Path(*parts[:i]))
    return real


def _link(top: Path, dest: Path) -> None:
    for rel in links():
        src, there = top / rel, dest / rel
        if not src.exists() or there.exists() or there.is_symlink():
            continue
        try:
            there.parent.mkdir(parents=True, exist_ok=True)
            there.symlink_to(src)
        except OSError as e:
            log.warning("chat worktree: could not link %s into %s (%s)", rel, dest, e)


def make(cwd: str, session: str) -> str:
    """The folder a fresh chat `session` starting in `cwd` should run in: a
    new worktree when `cwd` opts in, else `cwd` (also when the worktree
    could not be made, logged)."""
    if not opted_in(cwd) or not session:
        return cwd
    top = Path(_real(cwd))
    short = session.replace("-", "")[:8]
    branch = BRANCH_PREFIX + short
    dest = top / DIR / f"chat-{short}"
    if dest.is_dir():  # a resumed or re-sent start of the same chat
        return str(dest)
    base = "main" if new_project._git("-C", str(top), "rev-parse", "--verify", "--quiet",
                                      "refs/heads/main")[0] else "HEAD"
    ok, why = new_project._git("-C", str(top), "worktree", "add", "--quiet", "-b", branch,
                               str(dest), base, timeout=120)
    if not ok:
        log.warning("chat worktree: %s stays in %s (git worktree add: %s)",
                    session[:8], top, why)
        return cwd
    new_project._exclude(top)
    _link(top, dest)
    log.info("chat worktree: %s works in %s on %s", session[:8], dest, branch)
    return str(dest)
