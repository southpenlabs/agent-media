"""This server's own voices (Kokoro, Piper) in the voice picker, and routed
to their engines when chosen (docs/proposals/2026-11-14-voice-picker-providers.md,
step 1). No engine server is reached: the conftest answers for none, and
these tests put their own answers in."""

from __future__ import annotations

from agent_media_core.render import device as device_voice
from agent_media_core.render import local_voices
from agent_media_core.types import Target


def _serve(monkeypatch, kokoro=None, piper=None, chatterbox=None, orpheus=None):
    monkeypatch.setattr(local_voices, "_ASK", {"kokoro": lambda base: kokoro,
                                               "piper": lambda base: piper,
                                               "chatterbox": lambda base: chatterbox,
                                               "orpheus": lambda base: orpheus})
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

    monkeypatch.setattr(local_voices, "_ASK", {"kokoro": kokoro, "piper": lambda b: None,
                                               "chatterbox": lambda b: None,
                                               "orpheus": lambda b: None})
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


def test_the_piper_catalog_is_kept_a_day_with_each_voices_size(monkeypatch, tmp_path):
    raw = {"en_GB-cori-high": {"files": {"a.onnx": {"size_bytes": 60}, "a.json": {"size_bytes": 3}}},
           "en_US-amy-low": {"files": {"b.onnx": {"size_bytes": 20}}}}
    calls = []
    monkeypatch.setattr(local_voices, "_fetch_piper_catalog", lambda: calls.append(1) or raw)
    assert local_voices.piper_catalog() == {"en_GB-cori-high": 63, "en_US-amy-low": 20}
    assert local_voices.piper_catalog() == {"en_GB-cori-high": 63, "en_US-amy-low": 20}
    assert calls == [1], "kept"


def test_undownloaded_voices_are_offered_with_their_size(monkeypatch):
    _serve(monkeypatch, kokoro=None, piper=["en_GB-cori-high"])
    monkeypatch.setattr(local_voices, "KOKORO_VOICES", ("af_alloy", "bf_emma"))
    monkeypatch.setattr(local_voices, "piper_catalog",
                        lambda: {"en_GB-cori-high": 63, "en_GB-alba-medium": 50})
    by = {v["name"]: v for v in local_voices.offered()}
    assert by["kokoro:af_alloy"]["installed"] is False
    assert by["kokoro:af_alloy"]["size_bytes"] == local_voices.KOKORO_DOWNLOAD_BYTES
    assert by["piper:en_GB-cori-high"]["installed"] is True
    assert (by["piper:en_GB-alba-medium"]["installed"], by["piper:en_GB-alba-medium"]["size_bytes"]) == (False, 50)
    # Only installed ones render.
    assert [v["name"] for v in local_voices.voices()] == ["piper:en_GB-cori-high"]


def test_a_panes_own_voice_does_not_beat_the_apps_choice(monkeypatch):
    """A reply from a tmux pane carries the pane's workspace voice
    (voice_from "session"); the voice chosen in the app still wins, as it
    does for a thinking readout, which carries none (15 Nov 2026: Alloy
    chosen, the reply said in Libby)."""
    from agent_media_core import session_voice
    from agent_media_core.intake import submit
    from agent_media_core.types import Event, Source

    monkeypatch.delenv("MEDIA_RENDER_ENGINE", raising=False)
    monkeypatch.delenv("CLAUDE_TTS_ENGINE", raising=False)
    monkeypatch.setattr(session_voice, "voice_for", lambda s: None)
    sas = Target(name="sasonica")
    device_voice.set_override("sasonica", "server", "kokoro:af_alloy")
    reply = Event(source=Source.CLAUDE_CODE, text="Hi.", voice="en-GB-LibbyNeural",
                  metadata={"voice_from": "session"})
    assert submit._engine_and_voice(reply, sas) == ("kokoro", "af_alloy")
    thinking = Event(source=Source.CLAUDE_CODE, text="Hmm.")
    assert submit._engine_and_voice(thinking, sas) == ("kokoro", "af_alloy")
    # A voice the event really names still wins.
    own = Event(source=Source.CLAUDE_CODE, text="Hi.", voice="en-US-AvaNeural")
    assert submit._engine_and_voice(own, sas) == ("edge", "en-US-AvaNeural")
    # With no choice in the app, the pane's voice is used as before.
    device_voice.set_override("sasonica", "phone", None)
    engine, voice = submit._engine_and_voice(reply, Target(name="rooms"))
    assert voice == "en-GB-LibbyNeural"


def test_the_slow_voices_are_listed_slow_and_found_without_starting_them(monkeypatch, tmp_path):
    """Chatterbox and Orpheus (voice-picker step 6): their servers unload when
    idle, so an installed unit means installed, and nothing is started to
    find out; each voice says it is not live, with the trial's speed."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setattr(local_voices, "_get_json",
                        lambda url: (_ for _ in ()).throw(OSError("refused")))
    assert local_voices._ask_slow("orpheus", "http://o") is None, "no unit: not installed"
    units = tmp_path / "config" / "systemd" / "user"
    units.mkdir(parents=True)
    (units / "agent-media-orpheus-tts.service").write_text("[Unit]\n")
    (units / "agent-media-chatterbox-tts.service").write_text("[Unit]\n")
    assert local_voices._ask_slow("orpheus", "http://o") == list(local_voices.ORPHEUS_VOICES)
    clones = local_voices.chatterbox_voices_dir()
    clones.mkdir(parents=True)
    (clones / "natasha.wav").write_bytes(b"RIFF")
    (clones / "natasha.json").write_text('{"locale": "en-AU", "gender": "Female"}')
    assert local_voices._ask_slow("chatterbox", "http://c") == ["default", "natasha"]
    natasha = local_voices.chatterbox_voice("natasha")
    assert (natasha["name"], natasha["label"], natasha["locale"], natasha["accent"]) == (
        "chatterbox:natasha", "Natasha (clone)", "en-AU", "Australia")
    assert (natasha["live"], natasha["rtf"]) == (False, 3.5)
    tara = local_voices.orpheus_voice("tara")
    assert (tara["label"], tara["gender"], tara["live"], tara["rtf"]) == ("Tara", "Female", False, 7.0)
    assert local_voices.orpheus_voice("nobody") is None
    assert local_voices.split("orpheus:tara") == ("orpheus", "tara")


def test_slow_voices_not_here_are_offered_at_their_size(monkeypatch):
    _serve(monkeypatch)
    monkeypatch.setattr(local_voices, "OFFER_TO_DOWNLOAD", ("chatterbox", "orpheus"))
    monkeypatch.setattr(local_voices, "piper_catalog", lambda: {})
    by = {v["name"]: v for v in local_voices.offered()}
    assert by["orpheus:tara"]["installed"] is False
    assert by["orpheus:tara"]["size_bytes"] == local_voices.SLOW_DOWNLOAD_BYTES["orpheus"]
    assert by["chatterbox:default"]["size_bytes"] == local_voices.SLOW_DOWNLOAD_BYTES["chatterbox"]


def test_a_slow_voice_renders_whole_and_never_says_a_thinking_line(monkeypatch):
    from agent_media_core import session_voice
    from agent_media_core.intake import submit
    from agent_media_core.types import Event, Source

    monkeypatch.setenv("MEDIA_RENDER_VOICE_EDGE", "en-AU-NatashaNeural")
    monkeypatch.delenv("MEDIA_RENDER_ENGINE", raising=False)
    monkeypatch.delenv("CLAUDE_TTS_ENGINE", raising=False)
    monkeypatch.setattr(session_voice, "voice_for", lambda s: None)
    sas = Target(name="sasonica")
    device_voice.set_override("sasonica", "server", "orpheus:tara")
    reply = Event(source=Source.CLAUDE_CODE, text="Hi.")
    assert submit._engine_and_voice(reply, sas) == ("orpheus", "tara")
    for md in ({"thinking": True}, {"ask": True}, {"alert": True}):
        ev = Event(source=Source.CLAUDE_CODE, text="Hmm.", metadata=md)
        assert submit._engine_and_voice(ev, sas) == ("edge", "en-AU-NatashaNeural"), md
    assert submit._slow_engine("orpheus") and submit._slow_engine("chatterbox")
    assert not submit._slow_engine("kokoro")
    assert submit._render_workers(10, "orpheus") == 1
    assert submit._ext_for("orpheus") == submit._ext_for("chatterbox") == "wav"
