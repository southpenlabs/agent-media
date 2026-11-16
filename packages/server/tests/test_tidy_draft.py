"""`POST /voice/tidy/draft` (tidy_draft.py): the next message to a person written
from the chat and a hint (David, 16 Nov 2026). The model is never called:
`_summary._chat` is replaced by a stand-in.
"""

from __future__ import annotations

import os

import pytest

from agent_media_core.intake import _summary, fast
from agent_media_server import tidy_draft
from test_contract import AUTH, call, server, signed_in, typed  # noqa: F401 — fixtures

S1 = "0a1b2c3d-0000-4000-8000-000000000001"
CHAT = [
    {"from": "them", "text": "Are you coming on Saturday?", "ts": 1763200000000},
    {"from": "me", "text": "prob yeah, what time"},
    {"from": "Anna", "text": "From 7, bring nothing"},
]


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    for k in list(os.environ):
        if k.startswith(("MEDIA_", "RELAY_")):
            monkeypatch.delenv(k, raising=False)
    for d in ("HOME", "XDG_STATE_HOME", "XDG_CACHE_HOME", "XDG_CONFIG_HOME"):
        monkeypatch.setenv(d, str(tmp_path / d.lower()))
    monkeypatch.setenv("AGENT_PRIVATE_DIR", str(tmp_path / "private.d"))
    fast._reset_for_tests()


@pytest.fixture
def model(monkeypatch):
    state = {"answer": '{"draft": "  \\"cool see you at 7\\"  "}', "asked": []}

    def chat(system, user, timeout, model=None):
        state["asked"].append({"system": system, "user": user, "model": model})
        return state["answer"]

    monkeypatch.setattr(_summary, "_chat", chat)
    return state


def test_the_prompt_carries_the_chat_in_order_the_hint_and_the_register(model):
    got = tidy_draft.draft(tidy_draft._context(CHAT), "Anna", "um yeah say ill be there", "friendly")
    assert got == {"draft": "cool see you at 7"}
    asked = model["asked"][0]
    user = asked["user"]
    lines = [user.index(s) for s in ("Anna: Are you coming on Saturday?", "Me: prob yeah, what time",
                                     "Anna: From 7, bring nothing")]
    assert lines == sorted(lines)
    assert "<hint>um yeah say ill be there</hint>" in user and "For: Anna" in user
    from agent_media_server import tidy_intents

    assert tidy_intents.pattern(tidy_intents.get("friendly")) in asked["system"]
    assert "Never invent" in asked["system"] and "language the chat is in" in asked["system"]


@pytest.mark.parametrize("answer, want", [
    ('{"draft": "Draft: Sounds good"}', "Sounds good"),
    ("Here's the message:\n\"Sounds good\"", "Sounds good"),
    ("```\nMe: Sounds good\n```", "Sounds good"),
])
def test_a_draft_comes_back_stripped(model, answer, want):
    model["answer"] = answer
    assert tidy_draft.draft(tidy_draft._context(CHAT))["draft"] == want


@pytest.mark.parametrize("answer", ['{"draft": ""}', "", '{"draft": "   "}', "{broken"])
def test_no_draft_is_no_answer(model, answer):
    model["answer"] = answer
    assert tidy_draft.draft(tidy_draft._context(CHAT)) == {"draft": "", "why": "no_answer"}


def test_a_private_thread_reaches_only_a_private_model(model, monkeypatch):
    monkeypatch.setenv("MEDIA_FAST_MODELS", "hosted")
    assert tidy_draft.draft(tidy_draft._context(CHAT), private=True)["why"] == "no_answer"
    assert model["asked"] == []
    monkeypatch.setenv("MEDIA_PRIVATE_MODELS", "local")
    got = tidy_draft.draft(tidy_draft._context(CHAT), private=True)
    assert got["draft"] and [a["model"] for a in model["asked"]] == ["local"]


def test_the_model_is_the_tidy_one(model, monkeypatch):
    monkeypatch.setenv("MEDIA_FAST_MODELS", "fast-one")
    monkeypatch.setenv("MEDIA_TIDY_MODEL", "tidy-one")
    tidy_draft.draft(tidy_draft._context(CHAT))
    assert model["asked"][0]["model"] == "tidy-one"


def test_suggest_returns_guarded_replies_beside_the_draft(model):
    long = "x" * 81
    model["answer"] = ('{"draft": "See you at 7", "replies": ["Yes!", "yes", "Not yet", "\\"What should I bring?\\"", '
                       f'"{long}", "", 3, "Maybe"]}}')
    got = tidy_draft.draft(tidy_draft._context(CHAT), suggest=3)
    assert got == {"draft": "See you at 7", "replies": ["Yes!", "Not yet", "What should I bring?"]}
    assert "up to 3 one-tap replies" in model["asked"][0]["system"]


def test_suggest_without_a_hint_may_leave_the_draft_out(model):
    model["answer"] = '{"draft": "", "replies": ["Yes", "No"]}'
    assert tidy_draft.draft(tidy_draft._context(CHAT), suggest=2) == {"replies": ["Yes", "No"]}
    # With a hint a draft is wanted.
    got = tidy_draft.draft(tidy_draft._context(CHAT), hint="tell her yes", suggest=2)
    assert got["why"] == "no_answer" and got["replies"] == ["Yes", "No"]
    model["answer"] = '{"draft": "", "replies": []}'
    assert tidy_draft.draft(tidy_draft._context(CHAT), suggest=2)["why"] == "no_answer"


def test_the_context_is_trimmed_to_the_last_thirty(model):
    many = [{"from": "them", "text": f"message {i}"} for i in range(40)]
    tidy_draft.draft(tidy_draft._context(many))
    user = model["asked"][0]["user"]
    assert "message 9\n" not in user and "message 10" in user and "message 39" in user


def test_route(server, signed_in, model, tmp_path):
    before = sorted(p.relative_to(tmp_path) for p in tmp_path.rglob("*"))
    res, got = call(server, "POST", "/voice/tidy/draft",
                    {"context": CHAT, "recipient": "Anna", "hint": "", "session": S1, "suggest": 0}, AUTH)
    assert res.status == 200 and got == {"ok": True, "draft": "cool see you at 7"}, got
    # Nothing of the chat or the draft is kept.
    for p in tmp_path.rglob("*"):
        if p.is_file() and p.relative_to(tmp_path) not in before:
            body = p.read_bytes()
            assert b"Saturday" not in body and b"see you at 7" not in body, p


def test_route_validates(server, signed_in, model):
    for body in ({}, {"context": []}, {"context": "Anna: hi"}, {"context": [{"text": "hi"}]},
                 {"context": CHAT, "intent": "nope"}, {"context": CHAT, "suggest": 5},
                 {"context": CHAT, "suggest": True}, {"context": CHAT, "session": "../x"},
                 {"session": S1}):        # not a Matrix room here: the context is required
        res, got = call(server, "POST", "/voice/tidy/draft", body, AUTH)
        assert res.status == 400, (body, got)
    assert not model["asked"]


def test_route_needs_a_signed_in_caller(server, model):
    assert call(server, "POST", "/voice/tidy/draft", {"context": CHAT}, {})[0].status in (401, 403)
    assert not model["asked"]


def test_a_marked_private_thread_gets_no_hosted_model(server, signed_in, model, tmp_path, monkeypatch):
    d = tmp_path / "private.d"
    d.mkdir()
    (d / S1).write_text("")
    monkeypatch.setenv("MEDIA_FAST_MODELS", "hosted")
    res, got = call(server, "POST", "/voice/tidy/draft", {"context": CHAT, "session": S1}, AUTH)
    assert res.status == 200 and got["why"] == "no_answer" and model["asked"] == []


def test_a_rooms_messages_are_read_when_the_context_is_left_out(server, signed_in, model, monkeypatch):
    from agent_media_server import matrix

    class Room:
        def page(self, limit):
            return [{"role": "assistant", "parts": [{"text": "Dinner Friday?"}], "sender": {"name": "Bo"}},
                    {"role": "user", "parts": [{"text": "maybe"}], "sender": {"name": "David"}}], False

    monkeypatch.setattr(matrix, "room", lambda thread: Room() if thread == S1 else None)
    res, got = call(server, "POST", "/voice/tidy/draft", {"session": S1}, AUTH)
    assert res.status == 200 and got["draft"], got
    assert "Bo: Dinner Friday?\nMe: maybe" in model["asked"][0]["user"]


def test_intents_say_the_draft_route_is_there(server, signed_in):
    res, got = call(server, "GET", "/voice/tidy/intents", None, AUTH)
    assert res.status == 200 and got["draft"] is True
    assert "draft" not in [i["key"] for i in got["intents"]]
