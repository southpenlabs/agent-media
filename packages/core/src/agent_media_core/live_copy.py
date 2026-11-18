"""One live copy per session: who already runs a conversation, wherever.

Two `claude` processes on one session id write one transcript from two
histories, and the next resume reads a tangle. The guard used to be tmux's:
agent-workspace's `claude-tmux-session-register` and the
`~/.claude/tmux-sessions` registry refused a second resume *into a pane*,
and nothing guarded a session outside tmux (#491 step 4, the cross-repo
tmux-optional plan, ~/agent-config/docs/2026-11-15-tmux-optional.md).

The answer comes from Claude Code's own records instead
(`claude_sessions.records`, `~/.claude/sessions/<pid>.json`), which every
interactive claude writes and keeps current, in a pane, in a plain
terminal, or under sessiond — on Linux, a Mac or Windows (procinfo).

- sessiond asks before it resumes a Claude session (`Supervisor._spawn`)
  and refuses with `live_elsewhere` while a pane or a terminal holds it.
- Anything else asks `media session holder <id>` (rc 0: free, 3: held,
  with where on stdout; `--json`): agent-workspace's resume wrapper, in
  place of its tmux registry guard.

Never raises: a record that cannot be read is no holder.
"""

from __future__ import annotations

import os
from typing import Iterable, Optional
from agent_media_core import failures as _failures

#: Exit code of `media session holder` when the session is live somewhere.
HELD_RC = 3


def _by_sessiond(rec: dict) -> bool:
    """Is this claude one sessiond started? Its environment says so where it
    can be read (MEDIA_SESSIOND_SESSION); on Windows, where it cannot, the
    record's entrypoint does (sessiond runs `claude -p` through the SDK
    protocol: "sdk-cli"; a desk claude is "cli")."""
    from . import procinfo

    env = procinfo.environ(int(rec.get("pid") or 0))
    if env:
        return bool(env.get("MEDIA_SESSIOND_SESSION"))
    return str(rec.get("entrypoint") or "").startswith("sdk")


def holders(session: str, exclude_pids: Iterable[int] = ()) -> list[dict]:
    """Every live claude running `session`, but those in `exclude_pids`:
    `{"pid", "where": "pane"|"terminal"|"sessiond", "pane", "cwd"}`."""
    if not session:
        return []
    skip = {int(p) for p in exclude_pids if p}
    try:
        from . import claude_sessions
        recs = claude_sessions.records()
    except Exception as _exc:  # noqa: BLE001
        _failures.swallowed(_exc)
        return []
    out = []
    for rec in recs:
        if rec.get("session") != session or int(rec.get("pid") or 0) in skip:
            continue
        if int(rec.get("pid") or 0) == os.getpid():
            continue
        where = ("pane" if rec.get("pane")
                 else "sessiond" if _by_sessiond(rec) else "terminal")
        out.append({"pid": int(rec["pid"]), "where": where,
                    "pane": rec.get("pane") or "", "cwd": rec.get("cwd") or ""})
    return out


def holder(session: str, exclude_pids: Iterable[int] = (), *,
           count_sessiond: bool = True) -> Optional[dict]:
    """The first live copy of `session` (a pane first, then a terminal, then
    sessiond's), or None when it is free. sessiond passes
    `count_sessiond=False`: its own children are its business."""
    found = [h for h in holders(session, exclude_pids)
             if count_sessiond or h["where"] != "sessiond"]
    order = {"pane": 0, "terminal": 1, "sessiond": 2}
    found.sort(key=lambda h: order.get(h["where"], 3))
    return found[0] if found else None


def describe(h: dict) -> str:
    """"tmux pane %5", "a terminal (pid 123)", "the session host (pid 9)"."""
    if h.get("where") == "pane":
        return f"tmux pane {h.get('pane')}"
    if h.get("where") == "sessiond":
        return f"the session host (pid {h.get('pid')})"
    return f"a terminal (pid {h.get('pid')})"


def main(argv: list[str]) -> int:
    """`media session holder <id> [--json]`."""
    import argparse
    import json

    ap = argparse.ArgumentParser(prog="media session holder",
                                 description="Who runs this session now: rc 0 free, "
                                             f"{HELD_RC} live somewhere (one live copy "
                                             "per session).")
    ap.add_argument("session")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--exclude-pid", type=int, action="append", default=[],
                    help="a pid that does not count (the caller's own claude)")
    a = ap.parse_args(argv)
    found = holders(a.session, a.exclude_pid)
    if a.json:
        print(json.dumps({"session": a.session, "holders": found}))
    elif found:
        for h in found:
            print(describe(h))
    return HELD_RC if found else 0
