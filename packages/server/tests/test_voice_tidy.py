"""`POST /voice/tidy` (voice_tidy.py): an offer to clean up a dictated message
to a person (David, 25 Oct 2026). The model is never called: `_summary._chat`
is replaced by a stand-in; nothing is spoken: `voice_check.speak` is a recorder.
"""

from __future__ import annotations

import os
import time

import pytest

from agent_media_core.intake import _summary
from agent_media_server import voice_check, voice_tidy
from test_contract import AUTH, call, server, signed_in, typed  # noqa: F401 — fixtures

S1 = "0a1b2c3d-0000-4000-8000-000000000001"
MESSY = "um so i was thinking you know we could we could meet at 5 on friday"
TIDY = "I was thinking we could meet at 5 on Friday."


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
    state = {"answer": f'{{"messy": true, "tidy": "{TIDY}"}}', "asked": [], "delay": 0.0}

    def chat(system, user, timeout, model=None):
        state["asked"].append({"user": user, "model": model})
        if state["delay"]:
            time.sleep(state["delay"])
        a = state["answer"]
        if isinstance(a, Exception):
            raise a
        return a

    monkeypatch.setattr(_summary, "_chat", chat)
    return state


@pytest.fixture
def spoken(monkeypatch):
    said: list = []
    monkeypatch.setattr(voice_check, "speak", lambda session, text: said.append((session, text)))
    return said


@pytest.mark.parametrize("text", [
    "Can you pick up the milk on the way home?",
    "Running ten minutes late, see you soon.",
    "ok",
])
def test_clean_text_never_reaches_a_model(model, text):
    assert voice_tidy.looks_messy(text) is False
    got = voice_tidy.tidy(text)
    assert got == {"messy": False, "tidy": "", "why": "clean"} and not model["asked"]


@pytest.mark.parametrize("text", [
    MESSY,
    "I was going to um call you later",
    "meet me at the the station",
    "Can you -- no wait, can you bring the keys",
    "so we should probably leave around seven and then get there before the rush starts",
])
def test_messy_text_is_noticed(text):
    assert voice_tidy.looks_messy(text) is True


def test_a_messy_message_comes_back_tidy_with_the_recipient_and_chat(model):
    got = voice_tidy.tidy(MESSY, "Sam", "Sam: are you free this week?")
    assert got == {"messy": True, "tidy": TIDY}
    asked = model["asked"][0]["user"]
    assert "For: Sam" in asked and "are you free this week" in asked and MESSY in asked


@pytest.mark.parametrize("answer, why", [
    (None, "no_answer"), ("", "no_answer"), ("I tidied it", "no_answer"), (RuntimeError("x"), "no_answer"),
    ('{"messy": false, "tidy": ""}', "model_clean"),
    # unsafe: longer, a number lost; unchanged: the same words
    ('{"messy": true, "tidy": "' + MESSY + ' and I really hope that works for you, please say yes"}', "unsafe"),
    ('{"messy": true, "tidy": "I was thinking we could meet on Friday."}', "unsafe"),
    ('{"messy": true, "tidy": "' + MESSY + '"}', "unchanged"),
])
def test_anything_else_is_not_messy(model, answer, why):
    model["answer"] = answer
    got = voice_tidy.tidy(MESSY)
    assert got["messy"] is False and got["tidy"] == "" and got["why"] == why


def test_a_slow_model_is_not_waited_for(model, monkeypatch):
    monkeypatch.setenv("MEDIA_VOICE_TIDY_TIMEOUT", "0.6")
    model["delay"] = 2.0
    t0 = time.monotonic()
    got = voice_tidy.tidy(MESSY)
    assert time.monotonic() - t0 < 1.6 and got["messy"] is False


def test_switched_off_or_empty(model, monkeypatch):
    assert voice_tidy.tidy("  ")["why"] == "no_words"
    monkeypatch.setenv("MEDIA_VOICE_TIDY", "0")
    assert voice_tidy.tidy(MESSY)["why"] == "off" and not model["asked"]


def test_the_model_is_the_tidy_one_then_the_fast_list(model, monkeypatch):
    monkeypatch.setenv("MEDIA_FAST_MODELS", "fast-one")
    voice_tidy.tidy(MESSY)
    monkeypatch.setenv("MEDIA_TIDY_MODEL", "tidy-one")
    from agent_media_core.intake import fast

    fast._reset_for_tests()
    voice_tidy.tidy(MESSY)
    assert [a["model"] for a in model["asked"]] == ["fast-one", "tidy-one"]


def test_a_private_threads_words_reach_only_a_private_model(model, monkeypatch):
    monkeypatch.setenv("MEDIA_FAST_MODELS", "hosted")
    got = voice_tidy.tidy(MESSY, private=True)
    assert got["messy"] is False and model["asked"] == []        # no private model: sent as it is
    monkeypatch.setenv("MEDIA_PRIVATE_MODELS", "local")
    got = voice_tidy.tidy(MESSY, private=True)
    assert got["messy"] is True and [a["model"] for a in model["asked"]] == ["local"]


def test_route_speaks_a_tidy_in_the_thread_only_when_asked(server, signed_in, model, spoken):
    body = {"text": MESSY, "session": S1, "recipient": "Sam", "say": "Send this one? {tidy}"}
    res, got = call(server, "POST", "/voice/tidy", body, AUTH)
    assert res.status == 200 and got["messy"] is True and got["spoken"] is True, got
    assert spoken == [(S1, f"Send this one? {TIDY}")]
    spoken.clear()
    res, got = call(server, "POST", "/voice/tidy", {"text": MESSY, "session": S1}, AUTH)
    assert got["messy"] is True and got["spoken"] is False and not spoken
    res, got = call(server, "POST", "/voice/tidy", {**body, "text": "See you at the station at five."}, AUTH)
    assert got["messy"] is False and got["spoken"] is False and not spoken


def test_route_needs_a_signed_in_caller(server, model, spoken):
    assert call(server, "POST", "/voice/tidy", {"text": MESSY, "session": S1, "say": "{tidy}"}, {})[0].status in (401, 403)
    assert not model["asked"] and not spoken


def test_route_validates(server, signed_in, model, spoken):
    assert call(server, "POST", "/voice/tidy", {"session": S1}, AUTH)[0].status == 400
    assert call(server, "POST", "/voice/tidy", {"text": "x", "session": "../x"}, AUTH)[0].status == 400
    assert not model["asked"] and not spoken


def test_a_marked_private_thread_uses_the_private_models(server, signed_in, model, spoken, tmp_path, monkeypatch):
    d = tmp_path / "private.d"
    d.mkdir()
    (d / S1).write_text("")
    monkeypatch.setenv("MEDIA_FAST_MODELS", "hosted")
    res, got = call(server, "POST", "/voice/tidy", {"text": MESSY, "session": S1}, AUTH)
    assert got["messy"] is False and model["asked"] == []


def test_force_skips_only_the_messy_pre_check(model):
    clean = "Can you bring the salad on Sunday?"
    assert voice_tidy.tidy(clean)["why"] == "clean" and not model["asked"]
    model["answer"] = '{"messy": true, "tidy": "Can you bring the salad on Sunday, please?"}'
    got = voice_tidy.tidy(clean, "Mum", force=True)
    assert got["messy"] is True and got["tidy"].endswith("please?") and len(model["asked"]) == 1


def test_forced_nothing_to_change_says_so(model):
    clean = "Can you bring the salad on Sunday?"
    model["answer"] = '{"messy": false, "tidy": ""}'
    assert voice_tidy.tidy(clean, force=True)["why"] == "model_clean"
    model["answer"] = f'{{"messy": true, "tidy": "{clean}"}}'
    assert voice_tidy.tidy(clean, force=True) == {"messy": False, "tidy": "", "why": "unchanged"}


def test_forced_keeps_the_other_guards(model, monkeypatch):
    clean = "Meet me at 5 on Sunday at the station."
    # unsafe: longer, or a number lost
    model["answer"] = '{"messy": true, "tidy": "Meet me on Sunday at the station, and please do not be late, it matters."}'
    assert voice_tidy.tidy(clean, force=True)["why"] == "unsafe"
    model["answer"] = '{"messy": true, "tidy": "Meet me on Sunday at the station."}'
    assert voice_tidy.tidy(clean, force=True)["why"] == "unsafe"
    # failure and the off switch
    model["answer"] = None
    assert voice_tidy.tidy(clean, force=True)["why"] == "no_answer"
    monkeypatch.setenv("MEDIA_VOICE_TIDY", "0")
    assert voice_tidy.tidy(clean, force=True)["why"] == "off"


def test_forced_deadline_and_private_rule(model, monkeypatch):
    clean = "See you at the station."
    monkeypatch.setenv("MEDIA_VOICE_TIDY_TIMEOUT", "0.6")
    model["delay"] = 2.0
    t0 = time.monotonic()
    assert voice_tidy.tidy(clean, force=True)["messy"] is False and time.monotonic() - t0 < 1.6
    model["delay"] = 0.0
    model["answer"] = '{"messy": true, "tidy": "See you at the station!"}'
    monkeypatch.setenv("MEDIA_FAST_MODELS", "hosted")
    model["asked"].clear()
    assert voice_tidy.tidy(clean, private=True, force=True)["messy"] is False and model["asked"] == []
    monkeypatch.setenv("MEDIA_PRIVATE_MODELS", "local")
    assert voice_tidy.tidy(clean, private=True, force=True)["messy"] is True
    assert [a["model"] for a in model["asked"]] == ["local"]


def test_the_route_takes_force_only_as_true(server, signed_in, model):
    clean = "See you at the station."
    model["answer"] = '{"messy": true, "tidy": "See you at the station!"}'
    res, got = call(server, "POST", "/voice/tidy", {"text": clean, "force": True}, AUTH)
    assert res.status == 200 and got["messy"] is True, got
    for odd in ("yes", 1, None, False):
        res, got = call(server, "POST", "/voice/tidy", {"text": clean, "force": odd}, AUTH)
        assert got["messy"] is False and got["why"] == "clean"
