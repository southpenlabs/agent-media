"""Intents for the tidy offer (tidy_intents.py, voice_tidy.py `intent`): a
register to rewrite a message in, with a prompt from tidy_patterns/ (three from
Fabric, MIT, vendored at a pinned commit; the rest ours) inside our fixed rules.
The model is never called: `_summary._chat` is replaced by a stand-in.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

from agent_media_core.intake import _summary
from agent_media_server import tidy_intents, voice_tidy
from test_contract import AUTH, call, server, signed_in, typed  # noqa: F401 — fixtures

MSG = "Can you bring the salad on Sunday at 5? Mum said it is for the 12 of us."


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    for k in list(os.environ):
        if k.startswith(("MEDIA_", "RELAY_")):
            monkeypatch.delenv(k, raising=False)
    for d in ("HOME", "XDG_STATE_HOME", "XDG_CACHE_HOME", "XDG_CONFIG_HOME"):
        monkeypatch.setenv(d, str(tmp_path / d.lower()))
    monkeypatch.setenv("AGENT_PRIVATE_DIR", str(tmp_path / "private.d"))


@pytest.fixture
def model(monkeypatch):
    # Shorter than MSG, with its numbers: inside every intent's bound.
    state = {"answer": '{"messy": true, "tidy": "Could you bring the salad Sunday at 5? It is for the 12 of us."}',
             "asked": []}

    def chat(system, user, timeout, model=None):
        state["asked"].append({"system": system, "user": user, "model": model})
        return state["answer"]

    monkeypatch.setattr(_summary, "_chat", chat)
    return state


def test_the_list_is_the_servers_with_the_default_first():
    keys = [i["key"] for i in tidy_intents.listing()]
    assert keys[0] == tidy_intents.DEFAULT == "plain"
    assert keys == ["plain", "proofread", "polish", "professional", "friendly", "brief", "firm",
                    "apologetic", "decline", "request"]
    assert all(set(i) == {"key", "label"} and i["label"] for i in tidy_intents.listing())


def test_every_intent_has_a_pattern_and_the_fabric_ones_are_vendored_verbatim():
    for i in tidy_intents.INTENTS:
        assert tidy_intents.pattern(i), i.key
    fabric = {i.key: i.file for i in tidy_intents.INTENTS if i.source == "fabric"}
    assert fabric == {"proofread": "fabric/fix_typos.md", "polish": "fabric/improve_writing.md",
                      "professional": "fabric/create_formal_email.md"}
    d = Path(tidy_intents.__file__).parent / "tidy_patterns"
    lic = (d / "fabric" / "LICENSE").read_text()
    assert lic.startswith("MIT License") and "Permission is hereby granted" in lic
    readme = (d / "README.md").read_text()
    assert tidy_intents.FABRIC_SHA in readme
    assert (d / "fabric" / "improve_writing.md").read_text().startswith("# IDENTITY and PURPOSE")


@pytest.mark.parametrize("intent", [i.key for i in tidy_intents.INTENTS])
def test_each_intent_builds_its_pattern_inside_our_rules(model, intent):
    got = voice_tidy.tidy(MSG, "Mum", "Mum: are you free?", force=True, intent=intent)
    assert got["messy"] is True, got
    system = model["asked"][0]["system"]
    chosen = tidy_intents.get(intent)
    assert tidy_intents.pattern(chosen) in system
    # our rules follow the pattern, and say they win
    assert system.index(tidy_intents.pattern(chosen)) < system.index("RULES (they always win")
    for rule in ("Keep every fact, name, number and date", "no markdown", "plain text", "JSON only"):
        assert rule in system
    assert "For: Mum" in model["asked"][0]["user"] and MSG in model["asked"][0]["user"]


def test_length_bounds_are_per_intent(model):
    short = "Could you bring the salad on Sunday at 5? It is for the 12 of us."
    model["answer"] = f'{{"messy": true, "tidy": "{short}"}}'
    assert voice_tidy.tidy(MSG, force=True, intent="brief")["messy"] is True        # shorter: fine
    longer = MSG + " Thank you so much, it really means a lot to everyone here."
    model["answer"] = f'{{"messy": true, "tidy": "{longer}"}}'
    for intent in ("plain", "brief", "firm", "proofread"):
        assert voice_tidy.tidy(MSG, force=True, intent=intent)["why"] == "unsafe", intent
    grown = MSG + " Thank you."                     # a little longer: only intents that allow it
    model["answer"] = f'{{"messy": true, "tidy": "{grown}"}}'
    assert voice_tidy.tidy(MSG, force=True, intent="friendly")["messy"] is True
    assert voice_tidy.tidy(MSG, force=True, intent="brief")["why"] == "unsafe"


def test_the_guards_hold_for_every_intent(model):
    for intent in ("friendly", "brief", "professional"):
        model["answer"] = '{"messy": true, "tidy": "Could you bring the salad on Sunday? It is for the 12 of us."}'   # lost the 5
        assert voice_tidy.tidy(MSG, force=True, intent=intent)["why"] == "unsafe", intent
        model["answer"] = None
        assert voice_tidy.tidy(MSG, force=True, intent=intent)["why"] == "no_answer"
        model["answer"] = f'{{"messy": true, "tidy": "{MSG}"}}'
        assert voice_tidy.tidy(MSG, force=True, intent=intent)["why"] == "unchanged"


def test_markdown_and_preamble_are_stripped(model):
    model["answer"] = ('{"messy": true, "tidy": "Here\'s the revised message:\\n\\n```\\n'
                       '# Hi\\n**Could** you bring the salad on Sunday at 5? Mum said it is for the 12 of us.\\n```"}')
    got = voice_tidy.tidy(MSG, force=True, intent="professional")
    assert got["tidy"] == "Hi Could you bring the salad on Sunday at 5? Mum said it is for the 12 of us."
    assert "#" not in got["tidy"] and "*" not in got["tidy"] and "```" not in got["tidy"]
    assert tidy_intents.plain_text('"Could you come?"') == "Could you come?"
    assert tidy_intents.plain_text("- one\n- two") == "one\ntwo"
    assert tidy_intents.plain_text("Fine **bold** text") == "Fine bold text"


def test_a_private_thread_uses_only_private_models_whatever_the_intent(model, monkeypatch):
    monkeypatch.setenv("MEDIA_FAST_MODELS", "hosted")
    assert voice_tidy.tidy(MSG, force=True, intent="friendly", private=True)["messy"] is False
    assert model["asked"] == []
    monkeypatch.setenv("MEDIA_PRIVATE_MODELS", "local")
    assert voice_tidy.tidy(MSG, force=True, intent="friendly", private=True)["messy"] is True
    assert [a["model"] for a in model["asked"]] == ["local"]


def test_a_non_default_intent_skips_the_pre_check_but_plain_does_not(model):
    clean = "Can you bring the salad on Sunday?"
    assert voice_tidy.tidy(clean, intent="plain")["why"] == "clean" and not model["asked"]
    got = voice_tidy.tidy(clean, intent="friendly")
    assert len(model["asked"]) == 1 and got["why"] != "clean"


def test_the_routes(server, signed_in, model):
    res, body = call(server, "GET", "/voice/tidy/intents", None, AUTH)
    assert res.status == 200 and body["default"] == "plain"
    assert [i["key"] for i in body["intents"]][:3] == ["plain", "proofread", "polish"]
    res, body = call(server, "POST", "/voice/tidy", {"text": MSG, "intent": "friendly"}, AUTH)
    assert res.status == 200 and body["messy"] is True and "friendly" not in body
    res, body = call(server, "POST", "/voice/tidy", {"text": MSG, "intent": "sarcastic"}, AUTH)
    assert res.status == 400 and body["error"] == "unknown intent"
    res, _ = call(server, "POST", "/voice/tidy", {"text": MSG, "intent": 7}, AUTH)
    assert res.status == 400


def test_the_list_needs_a_signed_in_caller(server):
    assert call(server, "GET", "/voice/tidy/intents", None, {})[0].status in (401, 403)


def test_the_notice_names_what_is_vendored():
    notice = (Path(__file__).resolve().parents[3] / "NOTICE").read_text()
    assert "Fabric" in notice and "MIT" in notice and tidy_intents.FABRIC_SHA[:7] in notice
