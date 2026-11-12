"""A failing test told to the thread whose commit broke it.

David, 12 Nov 2026: "let's get that session notified about the keep reading
issue and ensure that notification is sent about these failed tests from
now on". A session that finds a red test whose cause is another session's
commit runs `media test-failure notify <test> --commit <sha> --why <cause>`:

- the commit's thread is its ``Thread:`` line (every commit in these repos ends
  with one), as a bare session id or, as they are actually written, a link:
  ``Thread: https://<host>/app/t/<id>``
- the message goes to that thread through the session host (`media session
  send`, signed ``[From <sender>]``); an ended thread is woken by it, with its
  own history, so the session that wrote the change can fix it
- once per test and commit, whoever finds it (``<state_dir>/test-failure-
  notices.json``), so ten sessions running the suite send one message

A commit with no thread line cannot be routed: the command says so, and the
finder tells David instead.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

from ._paths import state_dir

#: The id is taken from anywhere on the `Thread:` line, because every commit in
#: these repos writes the thread as its **link** --
#: `Thread: https://<host>/app/t/<id>` -- and this pattern once demanded a bare
#: id and nothing else. It therefore matched no commit in the repo, so a red
#: test could not be routed to the session that caused it even when the line
#: was there (found 13 Nov 2026, when a backup test's failure could not be
#: routed and the reason turned out not to be the missing line it looked like).
#: Both forms are read now.
_THREAD = re.compile(
    r"^Thread:.*?([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\s*$",
    re.M | re.I)


def _ledger_path() -> Path:
    return state_dir() / "test-failure-notices.json"


def _ledger() -> dict:
    try:
        d = json.loads(_ledger_path().read_text())
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _keep(d: dict) -> None:
    p = _ledger_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(f".tmp.{os.getpid()}")
    tmp.write_text(json.dumps(d, indent=1))
    tmp.replace(p)


def commit_info(commit: str, repo: str = ".") -> dict:
    """``{sha, short, subject, thread}`` for `commit` in `repo`; ValueError
    when git does not know it."""
    r = subprocess.run(["git", "-C", repo, "log", "-1", "--format=%H%n%h%n%s%n%B", commit],
                       capture_output=True, text=True, timeout=30)
    if r.returncode != 0 or not r.stdout.strip():
        raise ValueError(f"no commit {commit!r} in {repo}")
    sha, short, subject, body = (r.stdout.split("\n", 3) + ["", "", "", ""])[:4]
    m = _THREAD.search(body)
    return {"sha": sha, "short": short, "subject": subject, "thread": m.group(1) if m else None}


def message(test: str, info: dict, why: str, sender: str) -> str:
    subject = info["subject"] if len(info["subject"]) <= 110 else info["subject"][:107] + "…"
    lines = [f"[From {sender}] A test has failed since your commit {info['short']} ({subject}):",
             f"  {test}"]
    if why.strip():
        lines.append(f"Cause: {why.strip()}")
    lines.append("Please fix it there, or tell me if you think it is not yours. "
                 "(`media test-failure notify`; David, 12 Nov 2026: failed tests go to the thread that broke them.)")
    return "\n".join(lines)


def notify(test: str, commit: str, why: str = "", sender: str = "", repo: str = ".",
           send=None, now: Optional[float] = None) -> dict:
    """Tell `commit`'s thread that `test` fails because of it. ``{"sent":
    bool, "thread", "reason"?}``; `send(thread, sender, text)` delivers
    (default `media session send`)."""
    info = commit_info(commit, repo)
    if not info["thread"]:
        return {"sent": False, "thread": None, "reason": f"{info['short']} has no Thread: line; tell David"}
    key = f"{info['sha']}|{test}"
    led = _ledger()
    if key in led:
        return {"sent": False, "thread": info["thread"], "reason": "already told", "at": led[key].get("at")}
    sender = sender or os.environ.get("MEDIA_SESSIOND_SESSION", "")[:8] or "a session"
    text = message(test, info, why, sender)
    ok, said = (send or _send)(info["thread"], sender, text)
    if not ok:
        return {"sent": False, "thread": info["thread"], "reason": said or "the session host did not take it"}
    led[key] = {"at": time.time() if now is None else now, "thread": info["thread"], "why": why}
    _keep(led)
    return {"sent": True, "thread": info["thread"]}


def _send(thread: str, sender: str, text: str) -> tuple[bool, str]:
    r = subprocess.run([sys.executable, "-m", "agent_media_core.cli", "session", "send", thread,
                        "--from", sender, text], capture_output=True, text=True, timeout=60)
    return r.returncode == 0, (r.stderr or r.stdout or "").strip()[-300:]
