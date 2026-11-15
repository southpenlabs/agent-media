"""Moving an opencode chat to another folder (moves.py, sessiond `relocate`).

opencode files a session under the folder it was made in, and keeps working
there: a prompt sent from another folder, or a fork made from one, still runs
in the session's own (measured on 1.19.5, 15 Nov 2026). Its own export and
import do move it: `opencode export <id>` gives the session and its messages
as JSON, and `opencode import <file>` writes them back under the same id.
With the session's `directory` (and `projectID`) changed in between, the
chat keeps its id and its history and works in the new folder from its next
message on (measured: it read the new folder's file and remembered the first
thing it was asked).

The caller makes sure nothing is running the session while it moves: a
headless chat is detached by sessiond, a pane is closed by moves.py.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path

#: How long export or import may take (a long chat is a large file).
TIMEOUT_S = 120.0


def _git(cwd: str, *args: str) -> str:
    try:
        r = subprocess.run(["git", "-C", cwd, *args], capture_output=True, text=True,
                           timeout=10.0)
    except (OSError, subprocess.SubprocessError):
        return ""
    return r.stdout.strip() if r.returncode == 0 else ""


def project_id(cwd: str) -> str:
    """The id opencode gives `cwd`'s project: the one it already recorded
    for the folder's git top level, else what it caches in the repository
    (`<git dir>/opencode`), else the repository's first root commit (how it
    makes one), else `global` (no git, or no commits yet)."""
    from . import harnesses

    top = _git(cwd, "rev-parse", "--show-toplevel") or cwd
    rows = harnesses.opencode_rows("select id from project where worktree = ?", (top,))
    if rows:
        return str(rows[0][0])
    common = _git(cwd, "rev-parse", "--git-common-dir")
    if common:
        try:
            cached = (Path(cwd) / common / "opencode").read_text().strip()
            if cached:
                return cached
        except OSError:
            pass
    roots = sorted(r for r in _git(cwd, "rev-list", "--max-parents=0", "--all").split() if r)
    return roots[0] if roots else "global"


def move(session: str, cwd: str, *, program: str = "", env: dict | None = None) -> str:
    """File opencode session `session` under `cwd`: export, change its
    folder and project, import. "" when done, else why not."""
    from . import harnesses

    if not harnesses.is_opencode(session):
        return f"{session} is not an opencode session"
    if not os.path.isdir(cwd):
        return f"no such directory {cwd}"
    here = harnesses.opencode_rows("select directory from session where id = ?", (session,))
    if here and here[0][0] == cwd:
        return ""       # there already (sessiond moved it, or a second ask)
    exe = program or harnesses.program("opencode") or "opencode"
    run_env = dict(os.environ if env is None else env)
    try:
        out = subprocess.run([exe, "export", session], capture_output=True, text=True,
                             timeout=TIMEOUT_S, env=run_env, cwd=cwd)
    except (OSError, subprocess.SubprocessError) as e:
        return f"opencode export failed ({e})"
    text = out.stdout
    try:
        data = json.loads(text[text.find("{"):]) if "{" in text else None
    except ValueError:
        data = None
    if out.returncode != 0 or not isinstance(data, dict) or not isinstance(data.get("info"), dict):
        return f"opencode export gave no session ({(out.stderr or text).strip()[:200]})"
    if data["info"].get("id") != session:
        return "opencode export gave another session"
    data["info"]["directory"] = cwd
    data["info"]["projectID"] = project_id(cwd)
    fd, path = tempfile.mkstemp(prefix="opencode-move-", suffix=".json")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f)
        try:
            r = subprocess.run([exe, "import", path], capture_output=True, text=True,
                               timeout=TIMEOUT_S, env=run_env, cwd=cwd)
        except (OSError, subprocess.SubprocessError) as e:
            return f"opencode import failed ({e})"
        if r.returncode != 0:
            return f"opencode import failed ({(r.stderr or r.stdout).strip()[:200]})"
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass
    rows = harnesses.opencode_rows("select directory from session where id = ?", (session,))
    if not rows or rows[0][0] != cwd:
        return "opencode did not file it under the new folder"
    return ""
