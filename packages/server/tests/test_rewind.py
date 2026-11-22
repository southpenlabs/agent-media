"""`POST /session/rewind` (server-contract.md §6.64, rewind.py): a chat
taken back to one of its replies, in place. From a paired device at once; an
agent's request (the host's own token) only once a person approves it. The
fake claude and an in-process sessiond (test_headless); no real `claude`."""

from __future__ import annotations

import pytest

from agent_media_server import auth, branch, rewind, transcript
from test_headless import host, last_text, start, starts, state, wait_for  # noqa: F401 — fixtures


@pytest.fixture
def gate(monkeypatch):
    """A paired device has a bearer; the host's own token (an agent) has none."""
    monkeypatch.setattr(auth, "gate", lambda b: ("david", {}) if b else
                        (None, {"error": "who are you", "status": 401}))


def _two_turns(h):
    sid = start(h, "reply: plum")
    wait_for(lambda: last_text(h, sid) == "plum")
    h.sup.send(sid, "reply: pear")
    wait_for(lambda: last_text(h, sid) == "pear")
    wait_for(lambda: state(h, sid) == "waiting")
    msgs, _ = transcript.messages(sid)
    return sid, [m for m in msgs if m["role"] == "assistant"]


def _resumed_at(h) -> str:
    run = starts(h)[-1]["argv"]
    return run[run.index("--resume-session-at") + 1] if "--resume-session-at" in run else ""


def test_a_device_rewinds_at_once_and_the_next_start_resumes_there(host, gate):
    sid, replies = _two_turns(host)
    ok, d = rewind.request({"session": sid, "back": 1}, "tok")
    assert ok, d
    assert d["rewind"]["state"] == "done" and d["rewind"]["n"] == 2   # the prompt and its reply
    record = branch.fork_point(sid, replies[0]["id"])
    wait_for(lambda: state(host, sid) == "parked")
    assert host.sup.sessions[sid].resume_at == record
    # The next message resumes the same chat at that record, once.
    host.sup.send(sid, "reply: fig")
    wait_for(lambda: last_text(host, sid) == "fig")
    assert _resumed_at(host) == record
    assert starts(host)[-1]["argv"][starts(host)[-1]["argv"].index("--resume") + 1] == sid
    assert host.sup.sessions[sid].resume_at == ""
    argv = host.sup._argv(host.sup.sessions[sid], "claude", True)
    assert "--resume-session-at" not in argv
    # The next message through the server is told, once.
    assert rewind.with_note(sid, "hi").startswith(rewind.NOTE_PREFIX)
    assert rewind.with_note(sid, "hi") == "hi"


def test_an_agents_request_waits_for_a_person(host, gate):
    sid, replies = _two_turns(host)
    ok, d = rewind.request({"session": sid, "to": replies[0]["id"], "why": "a detour"}, "",
                           host=True)
    assert ok and d["pending"] and d["rewind"]["state"] == "asked", d
    assert d["rewind"]["by"] == "agent" and d["rewind"]["why"] == "a detour"
    assert host.sup.sessions[sid].resume_at == "" and state(host, sid) == "waiting"
    rid = d["rewind"]["id"]
    # The agent cannot answer its own request: that needs a paired device.
    ok, d = rewind.answer({"session": sid, "id": rid, "approve": True}, "")
    assert not ok and d["status"] == 401
    assert not rewind.answer({"session": sid, "id": "other", "approve": True}, "tok")[0]
    ok, d = rewind.answer({"session": sid, "id": rid, "approve": True}, "tok")
    assert ok and d["rewind"]["state"] == "done", d
    wait_for(lambda: state(host, sid) == "parked")
    assert host.sup.sessions[sid].resume_at == branch.fork_point(sid, replies[0]["id"])
    note = rewind.take_note(sid)
    assert note.startswith(rewind.NOTE_PREFIX) and "a detour" in note


def test_a_declined_request_changes_nothing_but_is_told(host, gate):
    sid, _replies = _two_turns(host)
    ok, d = rewind.request({"session": sid, "back": 1}, "", host=True)
    rid = d["rewind"]["id"]
    ok, d = rewind.answer({"session": sid, "id": rid, "approve": False}, "tok")
    assert ok and d["rewind"]["state"] == "declined"
    assert host.sup.sessions[sid].resume_at == "" and state(host, sid) == "waiting"
    assert rewind.take_note(sid).startswith(rewind.DECLINED_PREFIX)
    # Answered once: a second answer finds nothing waiting.
    assert rewind.answer({"session": sid, "id": rid, "approve": True}, "tok")[1]["code"] \
        == "not_pending"


def test_a_running_turn_is_waited_for(host, gate):
    sid, replies = _two_turns(host)
    record = branch.fork_point(sid, replies[0]["id"])
    s = host.sup.sessions[sid]
    with host.sup.lock:
        s.set_state("working")
    r = host.sup.rewind(sid, record)
    assert r["waiting"] and s.rewind_to == record and s.resume_at == "" and s.live
    # Its turn ends: the rewind is taken, the process parked.
    with host.sup.lock:
        s.set_state("waiting")
        host.sup._rewind_due(s)
    assert s.rewind_to == "" and s.resume_at == record
    wait_for(lambda: state(host, sid) == "parked")


def test_refusals(host, gate):
    sid, replies = _two_turns(host)
    assert rewind.request({"session": sid, "back": 1}, "")[1]["status"] == 401   # no host token
    assert rewind.request({"session": "nope", "back": 1}, "tok")[1]["status"] == 400
    assert rewind.request({"session": sid}, "tok")[1]["code"] == "bad_request"
    assert rewind.request({"session": sid, "back": 5}, "tok")[1]["code"] == "too_far"
    assert rewind.request({"session": sid, "to": "nope"}, "tok")[1]["code"] == "no_such_reply"
    # The newest reply has nothing after it.
    assert rewind.request({"session": sid, "to": replies[-1]["id"]}, "tok")[1]["code"] \
        == "nothing_to_rewind"
    assert rewind.cancel({"session": sid}, "tok")[1]["code"] == "not_pending"


def test_a_request_can_be_cancelled(host, gate):
    sid, _ = _two_turns(host)
    rewind.request({"session": sid, "back": 1}, "", host=True)
    ok, d = rewind.cancel({"session": sid}, "", host=True)
    assert ok and d["cancelled"] and rewind.view(sid) is None


def test_a_dry_run_counts_and_does_nothing(host, gate):
    sid, replies = _two_turns(host)
    ok, d = rewind.request({"session": sid, "to": replies[0]["id"], "dry_run": True}, "tok")
    assert ok and d["dry_run"] and d["n"] == 2 and d["to"] == replies[0]["id"], d
    assert rewind.view(sid) is None and host.sup.sessions[sid].resume_at == ""


def test_the_snapshot_carries_a_waiting_request(host, gate):
    from agent_media_server import thread_events

    sid, _ = _two_turns(host)
    rewind.request({"session": sid, "back": 1, "why": "x"}, "", host=True)
    ok, snap = thread_events.snapshot(sid, count_agents=False)
    assert ok and snap["rewind"]["state"] == "asked" and snap["rewind"]["why"] == "x"
