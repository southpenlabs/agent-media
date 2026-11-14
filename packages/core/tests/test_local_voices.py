"""This server's own voices (Kokoro, Piper) in the voice picker, and routed
to their engines when chosen (docs/proposals/2026-11-14-voice-picker-providers.md,
step 1). No engine server is reached: the conftest answers for none, and
these tests put their own answers in."""

from __future__ import annotations

from agent_media_core.render import device as device_voice
from agent_media_core.render import local_voices
from agent_media_core.types import Target


def _serve(monkeypatch, kokoro=None, piper=None):
    monkeypatch.setattr(local_voices, "_ASK", {"kokoro": lambda base: kokoro,
                                               "piper": lambda base: piper})
    local_voices.clear_cache()


def test_names_become_voices_the_picker_can_show():
    alloy = local_voices.kokoro_voice("af_alloy")
    assert alloy == {"name": "kokoro:af_alloy", "label": "Alloy", "gender": "Female",
                     "locale": "en-US", "language": "English", "accent": "United States",
                     "where": ["server"], "provider": "kokoro", "installed": True}
    assert local_voices.kokoro_voice("bm_george")["accent"] == "United Kingdom"
    assert local_voices.kokoro_voice("xx_nope") is None
    cori = local_voices.piper_voice("en_GB-cori-high")
    assert (cori["name"], cori["label"], cori["locale"], cori["accent"], cori["where"]) == (
        "piper:en_GB-cori-high", "Cori", "en-GB", "United Kingdom", ["server"])
    assert local_voices.piper_voice("en_GB-southern_english_female-low")["label"] == \
        "Southern English Female"
    assert local_voices.piper_voice("not-a-voice") is None


def test_only_what_a_running_server_has_is_offered(monkeypatch):
    _serve(monkeypatch)
    assert local_voices.voices() == []
    _serve(monkeypatch, kokoro=["af_alloy", "bf_emma"], piper=["en_GB-cori-high"])
    assert [v["name"] for v in local_voices.voices()] == [
        "kokoro:af_alloy", "kokoro:bf_emma", "piper:en_GB-cori-high"]


def test_an_answer_is_kept_a_while(monkeypatch):
    asked = []

    def kokoro(base):
        asked.append(base)
        return ["af_alloy"]

    monkeypatch.setattr(local_voices, "_ASK", {"kokoro": kokoro, "piper": lambda b: None})
    local_voices.clear_cache()
    local_voices.installed("kokoro")
    local_voices.installed("kokoro")
    assert asked == ["http://127.0.0.1:8880"]
    monkeypatch.setenv("MEDIA_KOKORO_BASE_URL", "http://box:9/")
    local_voices.installed("kokoro", fresh=True)
    assert asked[-1] == "http://box:9"


def test_an_old_kokoro_server_with_only_health_has_every_voice(monkeypatch):
    def get_json(url):
        raise OSError("404")

    monkeypatch.setattr(local_voices, "_get_json", get_json)
    monkeypatch.setattr(local_voices, "_get_ok", lambda url: url.endswith("/health"))
    assert local_voices._ask_kokoro("http://k") == list(local_voices.KOKORO_VOICES)
    monkeypatch.setattr(local_voices, "_get_ok", lambda url: (_ for _ in ()).throw(OSError()))
    assert local_voices._ask_kokoro("http://k") is None
    monkeypatch.setattr(local_voices, "_get_json", lambda url: {"voices": ["af_alloy"]})
    assert local_voices._ask_kokoro("http://k") == ["af_alloy"]
    monkeypatch.setattr(local_voices, "_get_json", lambda url: {"en_GB-cori-high": {}})
    assert local_voices._ask_piper("http://p") == ["en_GB-cori-high"]


def test_they_sit_with_their_accent_after_microsofts(monkeypatch):
    _serve(monkeypatch, kokoro=["bf_emma", "af_alloy"], piper=["en_GB-cori-high"])
    en = device_voice.languages()[0]
    gb = next(a for a in en["accents"] if a["locale"] == "en-GB")
    assert [v["name"] for v in gb["voices"]] == [
        "edge:en-GB-RyanNeural", "edge:en-GB-SoniaNeural",
        "kokoro:bf_emma", "piper:en_GB-cori-high"]
    us = next(a for a in en["accents"] if a["locale"] == "en-US")
    assert us["voices"][-1]["name"] == "kokoro:af_alloy"
    assert device_voice.find_voice("kokoro:af_alloy")["label"] == "Alloy"
    # Microsoft's and Google's say whose they are too.
    assert gb["voices"][0]["provider"] == "edge"
    assert device_voice.GOOGLE_VOICES[0]["provider"] == "google"


def test_a_chosen_local_voice_renders_with_its_engine(monkeypatch):
    from agent_media_core.intake import submit
    from agent_media_core.types import Event, Source

    monkeypatch.delenv("MEDIA_RENDER_ENGINE", raising=False)
    monkeypatch.delenv("CLAUDE_TTS_ENGINE", raising=False)
    sas = Target(name="sasonica")
    ev = Event(source=Source.CLAUDE_CODE, text="Hi.")
    device_voice.set_override("sasonica", "server", "kokoro:af_alloy")
    assert device_voice.server_choice("sasonica") == ("kokoro", "af_alloy")
    assert submit._engine_and_voice(ev, sas) == ("kokoro", "af_alloy")
    assert device_voice.server_voice("sasonica") == "af_alloy"
    device_voice.set_override("sasonica", "server", "piper:en_GB-cori-high")
    assert submit._engine_and_voice(ev, sas) == ("piper", "en_GB-cori-high")
    assert submit._ext_for("piper") == submit._ext_for("kokoro") == "wav"
    # An unknown provider is not routed anywhere: the env's engine.
    device_voice.set_override("sasonica", "server", "acme:x")
    assert device_voice.server_choice("sasonica") is None


def test_a_threads_local_voice_applies_while_the_server_renders(monkeypatch):
    from agent_media_core import session_voice
    from agent_media_core.intake import submit
    from agent_media_core.types import Event, Source

    monkeypatch.setenv("MEDIA_RENDER_VOICE_EDGE", "en-AU-NatashaNeural")
    monkeypatch.delenv("MEDIA_RENDER_ENGINE", raising=False)
    monkeypatch.delenv("CLAUDE_TTS_ENGINE", raising=False)
    monkeypatch.setattr(session_voice, "voice_for", lambda s: "kokoro:bf_emma")
    monkeypatch.setattr(submit, "_source_session", lambda md: "abc")
    sas = Target(name="sasonica")
    ev = Event(source=Source.CLAUDE_CODE, text="Hi.")
    device_voice.set_override("sasonica", "server", "edge:en-GB-RyanNeural")
    assert submit._engine_and_voice(ev, sas) == ("kokoro", "bf_emma")
    # The phone cannot say it: the target's own choice stands in.
    device_voice.set_override("sasonica", "phone", "edge:en-GB-RyanNeural")
    engine, _voice = submit._engine_and_voice(ev, sas)
    assert engine != "kokoro"
