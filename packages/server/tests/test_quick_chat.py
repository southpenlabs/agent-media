"""Quick chat (quick.py, roadmap item 26): a fast model answers, no agent.
Over real HTTP with the contract rig; the gateway call is faked and nothing
is spoken (MEDIA_QUICK_SPEAK=0)."""

from __future__ import annotations

import time

import pytest

from agent_media_server import app, quick

from test_contract import AUTH, call, server, shelf, signed_in, typed  # noqa: F401


@pytest.fixture(autouse=True)
def _quiet(monkeypatch):
    monkeypatch.setenv("MEDIA_QUICK_SPEAK", "0")
    monkeypatch.setenv("MEDIA_FAST_MODELS", "fast")
    quick._reset_for_tests()
    yield
    quick._reset_for_tests()


def _model(monkeypatch, answer="Paris.", seen=None):
    from agent_media_core.intake import _summary

    def chat(system, user, timeout, model=None):
        if seen is not None:
            seen.append({"user": user, "model": model})
        return answer
    monkeypatch.setattr(_summary, "_chat", chat)


def _settle(session, n):
    for _ in range(100):
        if len((quick.load(session) or {}).get("messages") or []) >= n and session not in quick._thinking:
            return
        time.sleep(0.02)


def test_a_quick_chat_starts_answers_and_is_listed(server, shelf, signed_in, typed, monkeypatch):
    seen: list = []
    _model(monkeypatch, "Paris.", seen)
    res, obj = call(server, "POST", "/ask", {"text": "capital of France?", "agent": "quick"}, AUTH)
    assert res.status == 200 and obj["agent"] == "quick", obj
    sid = obj["session"]
    _settle(sid, 2)
    assert typed == []                                  # no pane, nothing typed
    assert seen and seen[0]["model"] == "fast" and "Person: capital of France?" in seen[0]["user"]
    _, log = call(server, "GET", f"/conversation/log?session={sid}&messages=1", headers=AUTH)
    assert [(m["role"], m["parts"][0]["text"]) for m in log["messages"]] == \
        [("user", "capital of France?"), ("assistant", "Paris.")]
    _, targets = call(server, "GET", "/targets", headers=AUTH)
    row = {r["session"]: r for r in targets["sessions"]}[sid]
    assert row["harness"] == "quick" and row["title"] == "capital of France?" and row["recap"]["text"] == "Paris."


def test_a_reply_carries_the_conversation(server, shelf, signed_in, monkeypatch):
    seen: list = []
    _model(monkeypatch, "About 2.1 million.", seen)
    sid = quick.start("capital of France?")["session"]
    _settle(sid, 2)
    res, obj = call(server, "POST", "/reply", {"session": sid, "text": "how many people live there?"}, AUTH)
    assert res.status == 200, obj
    _settle(sid, 4)
    assert "Person: capital of France?" in seen[-1]["user"] and "how many people" in seen[-1]["user"]
    assert [m["role"] for m in quick.load(sid)["messages"]] == ["user", "assistant", "user", "assistant"]


def test_a_private_quick_chat_only_reaches_a_private_model(server, shelf, signed_in, monkeypatch):
    from agent_media_core import private

    seen: list = []
    _model(monkeypatch, "hosted answer", seen)
    monkeypatch.setattr(private, "hidden", lambda s: True)
    monkeypatch.delenv("MEDIA_PRIVATE_MODELS", raising=False)
    sid = quick.start("something private")["session"]
    _settle(sid, 2)
    assert seen == [] and "private" in quick.load(sid)["messages"][-1]["text"]
    monkeypatch.setenv("MEDIA_PRIVATE_MODELS", "local")
    call(server, "POST", "/reply", {"session": sid, "text": "and now?"}, AUTH)
    _settle(sid, 4)
    assert [s["model"] for s in seen] == ["local"]


def test_handoff_starts_an_agent_with_the_conversation(server, shelf, signed_in, typed, monkeypatch):
    from agent_media_server import send

    _model(monkeypatch, "Use a cron job.")
    sid = quick.start("how do I back up nightly?")["session"]
    _settle(sid, 2)
    asked = {}
    monkeypatch.setattr(send, "ask", lambda text, bearer, **k: (asked.update(text=text, **k) or True,
                                                                 {"session": "11111111-2222-4333-8444-555555555555"}))
    res, obj = call(server, "POST", "/session/handoff", {"session": sid, "agent": "codex"}, AUTH)
    assert res.status == 200 and obj["handed_to"] == "11111111-2222-4333-8444-555555555555"
    assert asked["agent"] == "codex" and "how do I back up nightly?" in asked["text"] and "Use a cron job." in asked["text"]
    res, obj = call(server, "POST", "/reply", {"session": sid, "text": "more"}, AUTH)
    assert res.status == 409 and obj["handed_to"]       # carry on in the agent's thread
    assert "/session/handoff" in app.CORS_PATHS


def test_tags_and_archive_work_on_a_quick_chat(server, shelf, signed_in, monkeypatch):
    _model(monkeypatch)
    sid = quick.start("hello")["session"]
    _settle(sid, 2)
    res, obj = call(server, "POST", "/session/tags", {"session": sid, "add": ["trivia"]}, AUTH)
    assert res.status == 200 and obj["tags"] == ["trivia"]
    res, obj = call(server, "POST", "/session/archive", {"session": sid, "archived": True}, AUTH)
    assert res.status == 200
    _, targets = call(server, "GET", "/targets", headers=AUTH)
    row = {r["session"]: r for r in targets["sessions"]}[sid]
    assert row["tags"] == ["trivia"] and row["archived"] is True
