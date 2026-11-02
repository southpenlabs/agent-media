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


# ── The changes behind the line: GET /session/git (3 Nov 2026) ────────────
#
# David, 3 Nov 2026: "Can we make that Git status line clickable? maybe we
# could see diffs from it. sort of like what Paseo does" — read-only. The
# files `git status` lists with their +/- counts against HEAD, the commits
# not yet pushed, and one file's diff. A diff is only ever asked for a path
# the status lists (or a file under an untracked folder it lists), so the
# app cannot read anything else in the folder.

MAX_FILES = 300
MAX_COMMITS = 50
MAX_DIFF = 200_000
_EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"


def _git(cwd: str, *args: str, ok: tuple[int, ...] = (0,)) -> str | None:
    try:
        p = subprocess.run(
            ["git", "--no-optional-locks", "-C", cwd, *args],
            capture_output=True, timeout=TIMEOUT_S * 2,
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0", "LC_ALL": "C"},
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if p.returncode not in ok:
        return None
    return p.stdout.decode("utf-8", "replace")


def _status_entries(cwd: str) -> list[dict] | None:
    """`git status -z` → [{"path", "from"?, "status"}]; None outside a checkout."""
    out = _git(cwd, "status", "--porcelain=v1", "-z")
    if out is None:
        return None
    parts = out.split("\0")
    entries: list[dict] = []
    i = 0
    while i < len(parts):
        rec = parts[i]
        i += 1
        if len(rec) < 4:
            continue
        xy, path = rec[:2], rec[3:]
        e = {"path": path, "status": xy.strip() or xy}
        if "R" in xy or "C" in xy:
            # -z puts the old name in the next record.
            e["from"] = parts[i] if i < len(parts) else ""
            i += 1
        entries.append(e)
    return entries


def _base(cwd: str) -> str:
    return "HEAD" if _git(cwd, "rev-parse", "--verify", "-q", "HEAD") is not None else _EMPTY_TREE


def changes(cwd: str | None) -> dict | None:
    """The status line's files and unpushed commits, or None outside a checkout."""
    if not cwd or not os.path.isdir(cwd):
        return None
    head = git_for(cwd)
    entries = _status_entries(cwd)
    if head is None or entries is None:
        return None
    base = _base(cwd)
    counts: dict[str, tuple[int | None, int | None]] = {}
    for line in (_git(cwd, "diff", "--no-ext-diff", "--numstat", "-M", base) or "").splitlines():
        bits = line.split("\t")
        if len(bits) < 3:
            continue
        name = bits[2]
        if " => " in name:
            # A rename: "a/{old => new}/b" or "old => new"; the new name is the one listed.
            if "{" in name:
                pre, rest = name.split("{", 1)
                mid, post = rest.split("}", 1)
                name = pre + mid.split(" => ", 1)[1] + post
            else:
                name = name.split(" => ", 1)[1]
        add, rem = bits[0], bits[1]
        counts[name] = (None, None) if add == "-" else (int(add), int(rem))
    files = []
    for e in entries[:MAX_FILES]:
        f = dict(e)
        if e["status"] == "??":
            f["untracked"] = True
        elif e["path"] in counts:
            add, rem = counts[e["path"]]
            if add is None:
                f["binary"] = True
            else:
                f["added"], f["removed"] = add, rem
        files.append(f)
    commits = []
    if head.get("ahead"):
        out = _git(cwd, "log", f"--max-count={MAX_COMMITS}", "--format=%h%x09%s", "@{upstream}..HEAD") or ""
        for line in out.splitlines():
            sha, _, subject = line.partition("\t")
            commits.append({"sha": sha, "subject": subject})
    return {**head, "files": files, "more": max(0, len(entries) - MAX_FILES), "commits": commits}


def file_diff(cwd: str | None, path: str) -> dict | None:
    """One listed file's diff against HEAD (an untracked one as all new), or
    None when `path` is not among the changes."""
    if not cwd or not os.path.isdir(cwd) or not path:
        return None
    entries = _status_entries(cwd)
    if not entries:
        return None
    entry = next((e for e in entries if e["path"] == path), None)
    if entry is None:
        # A file inside an untracked folder git lists as "dir/".
        if ".." in path.split("/"):
            return None
        entry = next((e for e in entries if e["status"] == "??" and e["path"].endswith("/")
                      and path.startswith(e["path"])), None)
        if entry is None:
            return None
    if path.endswith("/"):
        return None
    top = (_git(cwd, "rev-parse", "--show-toplevel") or "").strip()
    if not top:
        return None
    if entry["status"] == "??":
        full = os.path.realpath(os.path.join(top, path))
        if not full.startswith(os.path.realpath(top) + os.sep) or not os.path.isfile(full):
            return None
        out = _git(top, "diff", "--no-ext-diff", "--no-color", "--no-index", "--", "/dev/null", path, ok=(0, 1))
    else:
        paths = [path] + ([entry["from"]] if entry.get("from") else [])
        out = _git(top, "diff", "--no-ext-diff", "--no-color", "-M", _base(top), "--", *paths)
    if out is None:
        return None
    binary = "Binary files " in out and "@@" not in out
    truncated = len(out) > MAX_DIFF
    return {"path": path, "status": entry["status"], "diff": "" if binary else out[:MAX_DIFF],
            "binary": binary, "truncated": truncated}
