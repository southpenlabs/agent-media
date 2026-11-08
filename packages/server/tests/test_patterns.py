"""Patterns (patterns.py, §6.2.1): named prompts as markdown files, Fabric's
shape; a chat with people or a private thread read only to the private
models. No real model is called: fast.chat and fast.models are stubbed."""

from __future__ import annotations

import pytest

from agent_media_server import matrix, patterns
from test_contract import AUTH, SID, call, server, signed_in, typed  # noqa: F401 — fixtures


@pytest.fixture(autouse=True)
def _rig(monkeypatch, tmp_path):
    monkeypatch.setenv("MEDIA_PATTERNS_DIR", str(tmp_path / "mine"))
    monkeypatch.delenv("MEDIA_PATTERNS_FABRIC", raising=False)
    monkeypatch.delenv("MEDIA_PATTERNS_FABRIC_ONLY", raising=False)
    monkeypatch.setenv("AGENT_PRIVATE_DIR", str(tmp_path / "private.d"))
    monkeypatch.setattr(patterns.auth, "gate", lambda bearer: ({"username": "david"}, {}))
    monkeypatch.setattr(matrix, "room", lambda thread: None)


@pytest.fixture()
def asked(monkeypatch):
    from agent_media_core.intake import fast

    class Log(list):
        answers: dict

    log = Log()
    answers = {"out": "Sure, sounds good"}
    monkeypatch.setattr(fast, "models", lambda *a, private=False, **k: ["local"] if private else ["fast"])

    def chat(system, user, timeout, *, purpose_env=None, private=False):
        log.append({"system": system, "user": user, "private": private, "purpose": purpose_env})
        return answers["out"]
    monkeypatch.setattr(fast, "chat", chat)
    log.answers = answers
    return log


def _write(root, name, system, meta=None):
    d = root / name
    d.mkdir(parents=True)
    (d / "system.md").write_text(system)
    if meta is not None:
        (d / "meta.json").write_text(meta)


def test_built_ins_are_listed_with_their_kinds():
    ok, got = patterns.listing("tok")
    assert ok
    rows = {r["name"]: r for r in got["patterns"]}
    for name in ("shorter", "warmer", "formal"):
        assert rows[name]["kind"] == "tone" and rows[name]["source"] == "builtin"
    assert rows["more_like_this"]["kind"] == "answers"
    assert {rows[n]["kind"] for n in ("summarize", "todos", "explain")} == {"message"}
    assert rows["explain"]["label"] == "Explain simply"


def test_the_user_folder_adds_and_overrides_and_fabric_imports_as_is(tmp_path, monkeypatch):
    mine = tmp_path / "mine"
    _write(mine, "warmer", "Be very warm.")                       # no meta: still a tone
    _write(mine, "pirate", "Talk like a pirate.", '{"label": "Pirate", "kind": "tone"}')
    _write(mine, "bad name!", "x")
    fab = tmp_path / "fabric"
    _write(fab, "extract_wisdom", "# IDENTITY and PURPOSE\nYou extract wisdom.")
    _write(fab, "summarize", "Fabric's summarize.")
    _write(fab, "rate_content", "Rate it.")
    monkeypatch.setenv("MEDIA_PATTERNS_FABRIC", str(fab))
    monkeypatch.setenv("MEDIA_PATTERNS_FABRIC_ONLY", "extract_wisdom,summarize")
    cat = patterns.catalog()
    assert cat["warmer"].source == "user" and cat["warmer"].kind == "tone"
    assert cat["warmer"].label == "Warmer" and cat["warmer"].system() == "Be very warm."
    assert cat["pirate"].kind == "tone" and cat["pirate"].label == "Pirate"
    assert cat["extract_wisdom"].kind == "message" and cat["extract_wisdom"].label == "Extract wisdom"
    assert cat["summarize"].source == "fabric" and cat["summarize"].kind == "message"
    assert "rate_content" not in cat and "bad name!" not in cat


def test_a_tone_rewrites_through_the_fast_chain(asked):
    asked.answers["out"] = '"Sounds great, thank you!"'
    ok, got = patterns.run({"session": SID, "pattern": "warmer", "text": "ok",
                            "context": "Lunch at 1?"}, "tok")
    assert ok and got == {"pattern": "warmer", "kind": "tone", "text": "Sounds great, thank you!"}
    [a] = asked
    assert a["private"] is False and a["purpose"] == "MEDIA_PATTERN_MODEL"
    assert "<guidance>" in a["system"] and "warmer" in a["system"].lower()
    assert "Lunch at 1?" in a["user"] and "ok" in a["user"]


def test_more_like_this_answers_up_to_three_fresh_replies(asked):
    asked.answers["out"] = "1. Yes, let's do it\n- Commit it\nGo ahead and commit\nShip it now\nOne more"
    ok, got = patterns.run({"session": SID, "pattern": "more_like_this", "text": "Commit it",
                            "context": "Shall I commit?"}, "tok")
    assert ok and got["kind"] == "answers"
    assert got["replies"] == ["Yes, let's do it", "Go ahead and commit", "Ship it now"]


def test_a_message_pattern_uses_its_prompt_as_is(asked, tmp_path, monkeypatch):
    fab = tmp_path / "fabric"
    _write(fab, "extract_wisdom", "# IDENTITY\nYou extract wisdom.\n\n# INPUT\n\nINPUT:")
    monkeypatch.setenv("MEDIA_PATTERNS_FABRIC", str(fab))
    asked.answers["out"] = "- Ideas\n- More"
    ok, got = patterns.run({"session": SID, "pattern": "extract_wisdom", "text": "A long talk."}, "tok")
    assert ok and got["text"] == "- Ideas\n- More"
    assert asked[0]["system"] == "# IDENTITY\nYou extract wisdom.\n\n# INPUT\n\nINPUT:"
    assert asked[0]["user"] == "A long talk."


def test_a_rooms_text_goes_only_to_the_private_model(asked, monkeypatch):
    monkeypatch.setattr(matrix, "room", lambda thread: object() if thread == SID else None)
    for name in ("warmer", "more_like_this", "summarize"):
        ok, _ = patterns.run({"session": SID, "pattern": name, "text": "See you at 6, Ann"}, "tok")
        assert ok
    assert [a["private"] for a in asked] == [True, True, True]


def test_a_private_thread_goes_only_to_the_private_model(asked, tmp_path):
    (tmp_path / "private.d").mkdir()
    (tmp_path / "private.d" / SID).write_text("")
    patterns.run({"session": SID, "pattern": "shorter", "text": "a b c"}, "tok")
    assert asked[0]["private"] is True


def test_no_private_model_refuses_and_asks_nobody(asked, monkeypatch):
    from agent_media_core.intake import fast

    monkeypatch.setattr(matrix, "room", lambda thread: object())
    monkeypatch.setattr(fast, "models", lambda *a, private=False, **k: [] if private else ["fast"])
    ok, got = patterns.run({"session": SID, "pattern": "warmer", "text": "hi"}, "tok")
    assert not ok and got["status"] == 503 and got["reason"] == "no_private_model"
    assert "MEDIA_PRIVATE_MODELS" in got["error"]
    assert asked == []


def test_bad_asks(asked):
    assert patterns.run({"session": "x", "pattern": "warmer", "text": "hi"}, "tok")[1]["status"] == 400
    assert patterns.run({"session": SID, "pattern": "warmer", "text": " "}, "tok")[1]["status"] == 400
    assert patterns.run({"session": SID, "pattern": "../etc", "text": "hi"}, "tok")[1]["status"] == 404
    assert patterns.run({"session": SID, "pattern": "nope", "text": "hi"}, "tok")[1]["status"] == 404
    asked.answers["out"] = ""
    ok, got = patterns.run({"session": SID, "pattern": "warmer", "text": "hi"}, "tok")
    assert not ok and got["reason"] == "no_answer"


def test_the_text_is_never_logged(asked, caplog, capsys):
    secret = "the gate code is 4471"
    with caplog.at_level("DEBUG"):
        patterns.run({"session": SID, "pattern": "summarize", "text": secret}, "tok")
    out = capsys.readouterr()
    assert secret not in caplog.text and secret not in out.out and secret not in out.err


def test_the_routes(server, signed_in, asked, monkeypatch):  # noqa: F811
    monkeypatch.setattr(patterns.auth, "gate", lambda bearer: ({"username": "david"}, {}) if bearer == "tok"
                        else (None, {"error": "no", "status": 401}))
    res, body = call(server, "GET", "/patterns", headers=AUTH)
    assert res.status == 200 and any(r["name"] == "warmer" for r in body["patterns"])
    res, body = call(server, "POST", "/patterns/run", {"session": SID, "pattern": "shorter",
                                                       "text": "ok then"}, headers=AUTH)
    assert res.status == 200 and body["ok"] and body["text"] == "Sure, sounds good"
    assert call(server, "GET", "/patterns")[0].status == 401
