"""The fast-model chain (intake/fast.py): tried in order inside one deadline,
a model that errors skipped for a while, a private thread only to private
models. The gateway call is faked; nothing leaves the machine."""

from __future__ import annotations

import time

import pytest

from agent_media_core.intake import _summary, fast


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    for k in ("MEDIA_FAST_MODELS", "MEDIA_PRIVATE_MODELS", "MEDIA_FOLLOWUP_MODEL",
              "MEDIA_TAG_MODEL", "MEDIA_FAST_COOLDOWN"):
        monkeypatch.delenv(k, raising=False)
    fast._reset_for_tests()
    yield
    fast._reset_for_tests()


def _gateway(monkeypatch, answers: dict, delays: dict | None = None):
    asked: list = []

    def chat(system, user, timeout, model=None):
        asked.append(model)
        time.sleep((delays or {}).get(model, 0))
        a = answers.get(model)
        if isinstance(a, Exception):
            raise a
        return a
    monkeypatch.setattr(_summary, "_chat", chat)
    return asked


def test_order_purpose_then_fast_then_followup(monkeypatch):
    monkeypatch.setenv("MEDIA_TAG_MODEL", "a, b")
    monkeypatch.setenv("MEDIA_FAST_MODELS", "b,c")
    monkeypatch.setenv("MEDIA_FOLLOWUP_MODEL", "d")
    assert fast.models("MEDIA_TAG_MODEL") == ["a", "b", "c", "d"]
    assert fast.models() == ["b", "c", "d"]
    monkeypatch.delenv("MEDIA_FAST_MODELS")
    monkeypatch.delenv("MEDIA_FOLLOWUP_MODEL")
    assert fast.models() == [None]                      # the summary's own


def test_the_first_that_answers(monkeypatch):
    monkeypatch.setenv("MEDIA_FAST_MODELS", "dead,broke,good")
    asked = _gateway(monkeypatch, {"dead": None, "broke": RuntimeError("402"), "good": "hi"})
    assert fast.chat("s", "u", 5) == "hi"
    assert asked == ["dead", "broke", "good"]
    # The two that failed are skipped next time.
    asked.clear()
    assert fast.chat("s", "u", 5) == "hi" and asked == ["good"]


def test_all_down_are_tried_again(monkeypatch):
    monkeypatch.setenv("MEDIA_FAST_MODELS", "x")
    answers = {"x": None}
    asked = _gateway(monkeypatch, answers)
    assert fast.chat("s", "u", 5) is None
    answers["x"] = "back"
    assert fast.chat("s", "u", 5) == "back" and asked == ["x", "x"]


def test_too_slow_is_not_marked_down(monkeypatch):
    monkeypatch.setenv("MEDIA_FAST_MODELS", "slow,quick")
    asked = _gateway(monkeypatch, {"slow": "late", "quick": "q"}, {"slow": 1.5})
    t = time.monotonic()
    assert fast.chat("s", "u", 1.0) is None            # the deadline went on slow
    assert time.monotonic() - t < 1.4
    assert not fast._is_down("slow")                   # a longer ask may want it


def test_private_only_to_private_models(monkeypatch):
    monkeypatch.setenv("MEDIA_FAST_MODELS", "hosted")
    asked = _gateway(monkeypatch, {"hosted": "no", "local": "yes"})
    assert fast.chat("s", "u", 5, private=True) is None and asked == []
    monkeypatch.setenv("MEDIA_PRIVATE_MODELS", "local")
    assert fast.chat("s", "u", 5, private=True) == "yes" and asked == ["local"]
