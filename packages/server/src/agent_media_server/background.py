"""Background work still running in a thread whose turn is over (§6.1).

A thread whose main turn has ended is `waiting` — its reply is in, the
arrival notice, speech and follow-along all fire — but a background agent it
started (the Agent tool, `run_in_background`) or a background command (a
`run_in_background` Bash) may still be at work and will report back by
itself. The app labels such a thread working rather than "your turn"
("Working · 2 agents"); `state` stays `waiting`, the turn really is over.

    background   {"agents": N, "commands": M, "items": [...]} — on a
                 `waiting` row of `/sessions/state` and on the thread's
                 snapshot and `state` event, only while one of them is above
                 zero; absent (or null in the snapshot) otherwise

    items        what each one is, oldest first, at most ITEMS_MAX:
                 {"id", "kind": "agent"|"command", "description",
                 "started": epoch s or null}. `id` is what
                 `POST /threads/{session}/tasks/{id}/stop` takes
                 (agent_actions.task_stop, §6.12a). Plan 2026-11-16 item 1.

Where the counts come from, the same two places a move waits on
(moves.waiting_on):

- **A headless session**: sessiond's own task list (`task_list` of its
  view), every task it still hears from, a subagent or a background Bash;
  `local_bash` (or any shell kind) is a command, the rest agents.
- **A pane session**: the transcript's subagent files (agents.py, §6.12),
  `running` ones. A pane session's background Bash cannot be seen from
  here, so a pane thread's `commands` is always 0.
"""

from __future__ import annotations

import logging

log = logging.getLogger("agent-media.server.background")

#: A sessiond task whose `task_type` holds one of these is a command.
_COMMAND_KINDS = ("bash", "shell")

#: How many items a `background` carries: it rides on every row of
#: `/sessions/state`, and a strip shows a few.
ITEMS_MAX = 12


def kind_of(task: dict) -> str:
    """"command" for a background Bash (or any shell kind), else "agent"."""
    kind = str(task.get("type") or "").lower()
    return "command" if any(k in kind for k in _COMMAND_KINDS) else "agent"


def _item(id_: str, kind: str, description, started) -> dict:
    return {"id": id_, "kind": kind,
            "description": " ".join(str(description or "").split())[:200],
            "started": round(float(started), 3) if isinstance(started, (int, float)) else None}


def _sorted(items: list[dict]) -> list[dict]:
    return sorted(items, key=lambda i: (i["started"] is None, i["started"] or 0, i["id"]))[:ITEMS_MAX]


def of_tasks(tasks) -> dict | None:
    """`{"agents", "commands", "items"}` from sessiond's `task_list` rows, or
    None when there are none. A row with no id (a record from before
    task_list kept them) is counted but not listed: it cannot be stopped."""
    a = c = 0
    items = []
    for t in tasks or []:
        if not isinstance(t, dict):
            continue
        kind = kind_of(t)
        if kind == "command":
            c += 1
        else:
            a += 1
        if t.get("id"):
            items.append(_item(str(t["id"]), kind, t.get("description"), t.get("started")))
    return {"agents": a, "commands": c, "items": _sorted(items)} if a or c else None


def of_transcript(session: str, live: bool = True) -> dict | None:
    """`{"agents", "commands": 0, "items"}` from the transcript's running
    subagents (§6.12), or None when none is running or they cannot be read."""
    from . import agents

    try:
        running = [r for r in agents.agents(session, live=live) or []
                   if r.get("status") == "running"]
    except Exception:  # noqa: BLE001 — a count is a nicety; never fail a read
        log.exception("background %s: agents unreadable", session[:8])
        return None
    if not running:
        return None
    return {"agents": len(running), "commands": 0,
            "items": _sorted([_item(r["id"], "agent", r.get("description"), r.get("started_at"))
                              for r in running])}


def of_session(session: str, *, live: bool = True, tasks=None) -> dict | None:
    """What background work `session` has running: sessiond's `tasks` (its
    `task_list`) when the caller has them — a headless session — else the
    transcript's subagents. None when nothing runs or the session is not."""
    if not live or not session:
        return None
    if tasks is not None:
        return of_tasks(tasks)
    return of_transcript(session, live=True)
