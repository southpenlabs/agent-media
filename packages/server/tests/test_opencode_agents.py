"""An opencode thread's background agents (agents.opencode_agents): its
`task` tool's calls, each running in a child session. The part's shape is
what opencode 1.19.5 wrote on red2 (15 Nov 2026) for a `general` subagent."""

from __future__ import annotations

import json
import sqlite3

import pytest

from agent_media_server import agent_actions, agents

OC = "ses_e23e7b8d4ffeZNe9O2VSU7VpMI"
KID = "ses_e23e7aeccffersABhPYagy6lVS"
KID2 = "ses_e23e7aeccffersABhPYagy6lVT"
T0 = 1794693943605


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    path = tmp_path / "data" / "opencode" / "opencode.db"
    path.parent.mkdir(parents=True)
    with sqlite3.connect(path) as c:
        c.executescript("""
            create table session (id text primary key, parent_id text, directory text,
                title text, time_created integer, time_updated integer, time_archived integer);
            create table message (id text primary key, session_id text, time_created integer,
                data text);
            create table part (id text primary key, message_id text, session_id text,
                time_created integer, time_updated integer, data text);
        """)
        for sid, parent in ((OC, None), (KID, OC), (KID2, OC)):
            c.execute("insert into session values (?,?,'/p','t',?,?,null)", (sid, parent, T0, T0))
    return path


def put(path, sid, n, role, parts):
    mid = f"msg_{sid[-4:]}{n:03d}"
    with sqlite3.connect(path) as c:
        c.execute("insert into message values (?,?,?,?)",
                  (mid, sid, T0 + n * 1000, json.dumps({"role": role})))
        for k, p in enumerate(parts):
            c.execute("insert into part values (?,?,?,?,?,?)",
                      (f"prt_{sid[-4:]}{n:03d}{k:03d}", mid, sid, T0, T0, json.dumps(p)))


def task(child, status, **state):
    return {"type": "tool", "tool": "task", "callID": "call_" + child[-4:], "state": {
        "status": status, "title": "count files",
        "input": {"description": "count files", "prompt": "List the files", "subagent_type": "general"},
        "metadata": {"parentSessionId": OC, "sessionId": child},
        "time": {"start": T0, **({"end": T0 + 4145} if status != "running" else {})}, **state}}


def test_each_task_call_is_a_row_with_its_child_sessions_steps(db):
    put(db, OC, 0, "user", [{"type": "text", "text": "count them"}])
    put(db, OC, 1, "assistant", [task(KID, "completed"), task(KID2, "running")])
    put(db, KID, 0, "user", [{"type": "text", "text": "List the files"}])
    put(db, KID, 1, "assistant", [{"type": "tool", "tool": "bash", "state": {
        "status": "completed", "title": "ls", "time": {"start": T0 + 1000, "end": T0 + 2000}}},
        {"type": "text", "text": "Files: a.txt, b.txt"}])
    put(db, KID2, 1, "assistant", [{"type": "tool", "tool": "glob", "state": {
        "status": "running", "title": "**/*.py", "time": {"start": T0 + 1500}}}])
    done, running = agents.agents(OC, live=True)
    assert done["id"] == KID and done["status"] == "done" and done["steps"] == 1
    assert done["description"] == "count files" and done["agent_type"] == "general"
    assert done["ended_at"] == round((T0 + 4145) / 1000, 3) and done["current_step"] is None
    assert running["status"] == "running" and running["current_step"] == "**/*.py"
    assert agents.counts([done, running]) == {"running": 1, "total": 2}
    # The thread not running: its subagents died with the server.
    assert agents.agents(OC, live=False)[1]["status"] == "stopped"

    ok, log = agents.agent_log(OC, KID, limit=50)
    assert ok and log["agent"]["id"] == KID
    texts = [p.get("text") for m in log["messages"] for p in m.get("parts") or []
             if p.get("type") == "text"]
    assert "Files: a.txt, b.txt" in texts
    assert not agents.agent_log(OC, "ses_" + "z" * 26, limit=5)[0]


def test_an_aborted_task_is_stopped_and_a_failed_one_failed(db):
    put(db, OC, 1, "assistant", [task(KID, "error", error="Tool execution aborted"),
                                 task(KID2, "error", error="model refused")])
    a, b = agents.agents(OC, live=True)
    assert (a["status"], b["status"]) == ("stopped", "failed")


def test_no_messages_is_none_and_no_tasks_is_empty(db):
    assert agents.agents(OC, live=True) is None
    put(db, OC, 0, "user", [{"type": "text", "text": "hi"}])
    assert agents.agents(OC, live=True) == []


def test_a_message_to_one_asks_for_its_task_tool_with_its_id():
    words = agent_actions.message_words({"id": KID, "description": "count files"}, "also dirs")
    assert f"task_id {KID}" in words and "also dirs" in words and "SendMessage" not in words
