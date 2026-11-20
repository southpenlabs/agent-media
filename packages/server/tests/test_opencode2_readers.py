"""The server's opencode readers on opencode 2: the transcript, subagents and
a branch's cut read over its server (core opencode_api), never its tables.
The client is stood in for here (its own tests are core's
test_opencode_api); nothing reaches a real opencode."""

from __future__ import annotations

import pytest

from agent_media_core import opencode_api as api
from agent_media_server import agents, branch, transcript

SID = "ses_" + "p" * 26
KID = "ses_" + "k" * 26

MSGS = {
    SID: [
        {"id": "msg_1", "time": {"created": 1000}, "type": "user", "text": "count files"},
        {"id": "msg_2", "time": {"created": 2000}, "type": "assistant", "finish": "tool-calls",
         "content": [{"type": "tool", "id": "call_1", "name": "subagent", "state": {
             "status": "completed", "input": {"agent": "general", "description": "Count"},
             "content": [], "metadata": {"sessionID": KID, "status": "running"}}}]},
        {"id": "msg_3", "time": {"created": 3000}, "type": "assistant", "finish": "stop",
         "content": [{"type": "text", "text": "Two files."}]},
        {"id": "msg_4", "time": {"created": 3001}, "type": "idle", "outcome": "succeeded"},
        {"id": "msg_5", "time": {"created": 4000}, "type": "user", "text": "thanks"},
    ],
    KID: [{"id": "msg_k", "time": {"created": 2500}, "type": "assistant", "finish": "stop",
           "content": [{"type": "tool", "id": "g", "name": "glob",
                        "state": {"status": "completed", "input": {}, "content": []}}]}],
}


@pytest.fixture
def v2(monkeypatch):
    monkeypatch.setattr(api, "installed_v2", lambda env=None: True)
    monkeypatch.setattr(api, "messages", lambda sid, start=True: MSGS.get(sid))
    monkeypatch.setattr(api, "children", lambda sid, start=True: [
        {"id": KID, "parentID": SID, "title": "Count", "agent": "general",
         "outcome": "succeeded", "time": {"created": 2400, "updated": 2600, "idle": 2600}}]
        if sid == SID else [])
    monkeypatch.setattr(api, "service", lambda start=True: None)


def test_the_transcript_is_read_over_the_server(v2):
    msgs = transcript._opencode_messages(SID)
    roles = [m["role"] for m in msgs]
    assert roles == ["user", "assistant", "user"]
    reply = msgs[1]
    assert [p["type"] for p in reply["parts"]][-1] == "text"
    assert reply["parts"][-1]["text"] == "Two files."
    # The subagent call is the same part a v1 `task` call made.
    assert reply["parts"][0]["type"] == "tool" and reply["parts"][0]["name"] == "task"
    assert reply["parts"][0]["status"] == "done"


def test_subagents_are_child_sessions_with_their_own_outcome(v2):
    rows = agents.opencode_agents(SID, live=False)
    assert len(rows) == 1
    r = rows[0]
    assert r["id"] == KID and r["status"] == "done"
    assert r["description"] == "Count" and r["agent_type"] == "general"
    assert r["steps"] == 1 and r["started_at"] == pytest.approx(2.4)
    assert agents.opencode_agents("ses_" + "n" * 26, live=False) is None


def test_a_branch_cuts_at_v2_message_ids(v2):
    # From the reply: the fork keeps everything before the next prompt.
    assert branch.opencode_cut(SID, "msg_2", "history") == (True, "msg_5")
    # Before a prompt: that prompt itself; the first one keeps nothing.
    assert branch.opencode_cut(SID, "msg_5", "before") == (True, "msg_5")
    assert branch.opencode_cut(SID, "msg_1", "before") == (True, "")
    assert branch.opencode_cut(SID, "msg_nope", "history") == (False, "")


LATE = [
    {"id": "msg_1", "time": {"created": 1000}, "type": "user", "text": "count in the background"},
    {"id": "msg_2", "time": {"created": 2000}, "type": "assistant", "finish": "stop",
     "content": [{"type": "tool", "id": "call_1", "name": "subagent", "state": {
         "status": "completed", "input": {"agent": "general", "description": "Count"},
         "content": [], "metadata": {"sessionID": KID, "status": "running"}}},
         {"type": "text", "text": "I started a subagent to count them."}]},
    {"id": "msg_3", "time": {"created": 2001}, "type": "idle", "outcome": "succeeded"},
    # 2.3's report of a background child: a synthetic message that wakes the parent.
    {"id": "msg_4", "time": {"created": 5000}, "type": "synthetic", "description": "Count",
     "text": f'<subagent sessionID="{KID}" state="completed" description="Count"> </subagent>'},
    {"id": "msg_5", "time": {"created": 5100}, "type": "assistant", "finish": "stop",
     "content": [{"type": "text", "text": "It found two files."}]},
    {"id": "msg_6", "time": {"created": 5101}, "type": "idle", "outcome": "succeeded"},
]


def test_a_late_subagent_report_is_its_own_reply_and_not_the_persons_words(monkeypatch):
    """opencode 2.3 (#63382): the turn that started a subagent ends, and the
    child's `<subagent>` report wakes the chat later. The speech hook reads
    each turn's words since the last input, so the late turn is spoken as
    a reply of its own (a new message id, so `_already_spoken` lets it
    through once) without the first turn's words again; the report itself
    is opencode's, not a "You:" turn in the thread."""
    from agent_media_core import harnesses

    got: list = LATE[:3]
    monkeypatch.setattr(api, "installed_v2", lambda env=None: True)
    monkeypatch.setattr(api, "messages", lambda sid, start=True: list(got) if sid == SID else [])
    monkeypatch.setattr(api, "live_messages", lambda sid: [])
    monkeypatch.setattr(api, "children", lambda sid, start=True: [])
    monkeypatch.setattr(api, "service", lambda start=True: None)
    assert harnesses.opencode_last_reply(SID) == ("msg_2", "I started a subagent to count them.")
    got[:] = LATE
    assert harnesses.opencode_last_reply(SID) == ("msg_5", "It found two files.")
    msgs = transcript._opencode_messages(SID)
    assert [m["role"] for m in msgs] == ["user", "assistant", "assistant"]
    assert not any("<subagent" in str(p.get("text") or "") for m in msgs for p in m["parts"])
