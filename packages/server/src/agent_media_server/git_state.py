"""A thread's git branch and how many files are changed, for the status line
under its input box (David, 1 Nov 2026: "Git branch and changes").

`git_for(cwd)` runs one `git status --porcelain=v1 --branch` in the thread's
folder and answers `{"branch", "changed", "ahead", "behind"}`, or None when
the folder is not in a git checkout (or git is missing or slow). `branch` is
None on a detached HEAD. The answer is kept for CACHE_S per folder, so a
thread polled every few seconds costs one git call in that time, and a call
is cut off after TIMEOUT_S so a slow disk never holds up the thread's log.

Read-only: `--no-optional-locks` keeps git from refreshing the index, so it
never takes the lock a session's own `git commit` needs.
"""

from __future__ import annotations

import os
import re
import subprocess
import threading
import time

CACHE_S = 15.0
TIMEOUT_S = 2.0

_lock = threading.Lock()
_cache: dict[str, tuple[float, dict | None]] = {}

_AHEAD = re.compile(r"ahead (\d+)")
_BEHIND = re.compile(r"behind (\d+)")


def parse(out: str) -> dict:
    """`git status --porcelain=v1 --branch` output → the status dict."""
    lines = out.splitlines()
    branch = None
    ahead = behind = 0
    if lines and lines[0].startswith("## "):
        head = lines.pop(0)[3:]
        if head.startswith("No commits yet on "):
            branch = head[len("No commits yet on "):].strip() or None
        elif not head.startswith("HEAD (no branch)"):
            name = head.split("...", 1)[0].split(" [", 1)[0].strip()
            branch = name or None
        if (m := _AHEAD.search(head)):
            ahead = int(m.group(1))
        if (m := _BEHIND.search(head)):
            behind = int(m.group(1))
    changed = sum(1 for l in lines if l.strip())
    return {"branch": branch, "changed": changed, "ahead": ahead, "behind": behind}


def _read(cwd: str) -> dict | None:
    try:
        p = subprocess.run(
            ["git", "--no-optional-locks", "-C", cwd, "status", "--porcelain=v1", "--branch"],
            capture_output=True, text=True, timeout=TIMEOUT_S,
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0", "LC_ALL": "C"},
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if p.returncode != 0:
        return None
    return parse(p.stdout)


def git_for(cwd: str | None, now: float | None = None) -> dict | None:
    """The folder's git status (cached CACHE_S), or None outside a checkout."""
    if not cwd or not os.path.isdir(cwd):
        return None
    now = time.monotonic() if now is None else now
    with _lock:
        hit = _cache.get(cwd)
        if hit is not None and now - hit[0] < CACHE_S:
            return hit[1]
    val = _read(cwd)
    with _lock:
        _cache[cwd] = (now, val)
    return val
