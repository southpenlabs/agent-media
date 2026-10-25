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

S1 = "0a1b2c3d-0000-4000-8000-000000000001"
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
                    "apologetic", "decline", "request", "emoji"]
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


@pytest.mark.parametrize("intent", [i.key for i in tidy_intents.INTENTS if not i.emoji])   # emoji: its own tests below
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


# --- The emoji paragraph (David, 25 Oct 2026) ---------------------------------

SALAD = "can you bring the salad on sunday at 5 mum said its for the 12 of us"
EMOJI_SALAD = "🥗 Can you bring the salad\non Sunday at 5?\n\n👩 Mum said it's for\nthe 12 of us 🍽️"


def _answer(model, text):
    import json as _json
    model["answer"] = _json.dumps({"messy": True, "tidy": text})


def test_the_emoji_prompt_carries_his_examples_and_its_own_rules(model):
    _answer(model, EMOJI_SALAD)
    got = voice_tidy.tidy(SALAD, "Mum", force=True, intent="emoji")
    assert got["messy"] is True, got
    system = model["asked"][0]["system"]
    for example in ("🙂‍↕️ Yes,\n🙏 thank you", "🤟 Fuck\n. L 😝 yeah!", "✊ We got it!\n.L 😁"):
        assert example in system
    assert "🥗 Can you bring the salad" in system            # an ordinary message too
    assert "no new words" in system and "swearing" in system and "At most about one emoji per short line, plus two" in system
    assert "no markdown" in system


def test_lines_and_emoji_survive_the_clean_up(model):
    _answer(model, "```\n" + EMOJI_SALAD + "\n\n\n\n```")
    got = voice_tidy.tidy(SALAD, force=True, intent="emoji")
    assert got["tidy"] == EMOJI_SALAD
    _answer(model, "✊ We got it!\n.L 😁")
    assert voice_tidy.tidy("we got it", force=True, intent="emoji")["tidy"] == "✊ We got it!\n.L 😁"
    _answer(model, "🤟 Fuck\n. L 😝 yeah!")
    assert voice_tidy.tidy("fuck yeah", force=True, intent="emoji")["tidy"] == "🤟 Fuck\n. L 😝 yeah!"


def test_other_intents_still_flatten_lines(model):
    _answer(model, "Could you bring the salad\non Sunday at 5? It is for the 12 of us.")
    assert "\n" not in voice_tidy.tidy(SALAD, force=True, intent="polish")["tidy"]


def test_emoji_words_may_not_grow_or_change(model):
    _answer(model, "🥗 Can you bring the lovely salad\non Sunday at 5? 👩 Mum said it's for the 12 of us")
    assert voice_tidy.tidy(SALAD, force=True, intent="emoji")["why"] == "unsafe"       # a new word
    _answer(model, "🥗 Can you bring the salad\non Sunday? 👩 Mum said it's for the 12 of us")
    assert voice_tidy.tidy(SALAD, force=True, intent="emoji")["why"] == "unsafe"       # lost the 5


def test_emoji_count_is_capped(model):
    many = "🥗🥗🥗 Can you bring the salad 🥳🥳\non Sunday at 5? 🎉🎉🎉"
    _answer(model, many)
    assert tidy_intents.emoji_count(many) == 8
    assert voice_tidy.tidy(SALAD, force=True, intent="emoji")["why"] == "unsafe"
    # a ZWJ sequence with its selectors is one emoji
    assert tidy_intents.emoji_count("🙂‍↕️ Yes") == 1 and tidy_intents.emoji_count("👍🏽 ok") == 1


def test_the_spoken_prompt_has_the_words_not_emoji_names(server, signed_in, model, monkeypatch):
    from agent_media_server import voice_check

    said = []
    monkeypatch.setattr(voice_check, "speak", lambda session, text: said.append(text))
    _answer(model, "🤟 Fuck\n. L 😝 yeah!")
    res, body = call(server, "POST", "/voice/tidy", {"text": "fuck yeah", "session": S1, "intent": "emoji",
                                                     "say": "Send this one? {tidy}"}, AUTH)
    assert res.status == 200 and body["tidy"] == "🤟 Fuck\n. L 😝 yeah!"
    assert said == ["Send this one? Fuck, yeah!"]
    assert tidy_intents.speakable(EMOJI_SALAD) == "Can you bring the salad, on Sunday at 5? Mum said it's for, the 12 of us"
    assert tidy_intents.speakable("⏱️ Be there in 20. . L 👍") == "Be there in 20."
    assert tidy_intents.speakable("✊ We got it!\n.L 😁") == "We got it!"


def test_an_emoji_paragraph_reaches_a_matrix_room_with_its_lines(monkeypatch):
    """Sent to a room, the body keeps its line breaks (m.text body; Matrix clients
    and the SMS bridges show them as they are, so no formatted_body is needed)."""
    from agent_media_server import auth, matrix, send

    sent = []

    class Room:
        def send(self, body):
            sent.append(body)
            return True, {"ok": True}

    monkeypatch.setattr(matrix, "room", lambda s: Room() if s == S1 else None)
    monkeypatch.setattr(auth, "gate", lambda bearer: ({"username": "david"}, None))
    ok, _ = send.reply("", EMOJI_SALAD, "tok", session=S1)
    assert ok and sent == [EMOJI_SALAD]
