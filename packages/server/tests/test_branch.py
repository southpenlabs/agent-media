"""`POST /session/branch` (server-contract.md §6.34, branch.py): a new chat
from a reply, with its history (a fork of the Claude session up to that
reply, started by sessiond) or from its words (a fresh chat, quoted). The
fake claude and an in-process sessiond (test_headless); no real `claude`."""

from __future__ import annotations

import json

from agent_media_server import branch, transcript
from test_headless import host, last_text, start, starts, wait_for  # noqa: F401 — fixtures


def _rec(kind, uid, content, **kw):
    role = "assistant" if kind == "assistant" else "user"
    return {"type": kind, "uuid": uid, "message": {"role": role, "content": content}, **kw}


TRANSCRIPT = [
    _rec("user", "u1", "first question"),
    _rec("assistant", "a1", [{"type": "text", "text": "thinking aloud"}]),
    _rec("assistant", "a1b", [{"type": "tool_use", "id": "t1", "name": "Bash", "input": {}}]),
    _rec("user", "r1", [{"type": "tool_result", "tool_use_id": "t1", "content": "ok"}]),
    _rec("assistant", "a1c", [{"type": "text", "text": "the answer"}]),
    _rec("user", "m1", "a note", isMeta=True),
    _rec("assistant", "side", [{"type": "text", "text": "a subagent"}], isSidechain=True),
    _rec("user", "u2", [{"type": "text", "text": "second question"}]),
    _rec("assistant", "a2", [{"type": "text", "text": "second answer"}]),
]


def test_the_fork_point_is_the_end_of_that_reply(tmp_path, monkeypatch):
    p = tmp_path / "t.jsonl"
    p.write_text("\n".join(json.dumps(r) for r in TRANSCRIPT) + "\nnot json\n")
    monkeypatch.setattr(transcript, "transcript_path", lambda s: str(p))
    # The reply's words after its steps: a tool's result and a note do not end it.
    assert branch.fork_point("s", "a1") == "a1c"
    assert branch.fork_point("s", "a2") == "a2"
    # From the prompt, the reply that answers it.
    assert branch.fork_point("s", "u1") == "a1c"
    assert branch.fork_point("s", "nope") == ""
    monkeypatch.setattr(transcript, "transcript_path", lambda s: "")
    assert branch.fork_point("s", "a1") == ""


def test_a_branch_with_history_is_a_fork_started_by_sessiond(host, monkeypatch):
    src = start(host, "reply: plum")
    wait_for(lambda: last_text(host, src) == "plum")
    msgs, _ = transcript.messages(src)
    reply = msgs[-1]["id"]
    from agent_media_server import auth

    monkeypatch.setattr(auth, "gate", lambda b: ("david", None))
    ok, d = branch.session_branch(src, reply, "reply: pear", "tok")
    assert ok and d["branched"] and d["session"] != src, d
    assert d["from"] == {"session": src, "id": reply}
    run = starts(host)[-1]["argv"]
    assert run[run.index("--resume") + 1] == src and "--fork-session" in run
    assert run[run.index("--resume-session-at") + 1] == branch.fork_point(src, reply)
    assert run[run.index("--session-id") + 1] == d["session"]
    wait_for(lambda: last_text(host, d["session"]) == "pear")
    # Its next spawn resumes the branch itself, not the thread it came from.
    s = host.sup.sessions[d["session"]]
    argv = host.sup._argv(s, "claude", True)
    assert argv[argv.index("--resume") + 1] == d["session"] and "--fork-session" not in argv


def test_bad_branches(host, monkeypatch):
    from agent_media_server import auth

    monkeypatch.setattr(auth, "gate", lambda b: ("david", None))
    src = start(host, "reply: fig")
    wait_for(lambda: last_text(host, src) == "fig")
    assert branch.session_branch("nope", "x", "hi", "tok")[1]["status"] == 400
    assert branch.session_branch(src, "x", "  ", "tok")[1]["status"] == 400
    assert branch.session_branch(src, "x", "hi", "tok", mode="sideways")[1]["status"] == 400
    assert branch.session_branch(src, "not-there", "hi", "tok")[1]["status"] == 404
    other = "11111111-2222-3333-4444-555555555555"
    assert branch.session_branch(other, "x", "hi", "tok")[1]["status"] == 409


def test_a_branch_from_its_words_is_a_fresh_chat_quoted(host, monkeypatch):
    from agent_media_server import auth

    monkeypatch.setattr(auth, "gate", lambda b: ("david", None))
    src = start(host, "reply: kiwi")
    wait_for(lambda: last_text(host, src) == "kiwi")
    ok, d = branch.session_branch(src, "", "reply: lime", "tok", mode="quote", quote="kiwi")
    assert ok and d["branched"] and d["session"] != src, d
    run = starts(host)[-1]["argv"]
    assert "--fork-session" not in run and "--resume" not in run
