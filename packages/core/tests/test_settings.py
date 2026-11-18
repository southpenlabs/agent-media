"""settings.py: the speech settings, typed, parsed once
(docs/proposals/2026-11-19-typed-records-and-settings.md)."""

from __future__ import annotations

from agent_media_core import settings
from agent_media_core.settings import DEFAULT_LOUDNORM, Speech


def test_defaults_with_nothing_set():
    s = Speech.from_env({})
    assert s == Speech()
    assert (s.volume, s.volume_max, s.lock_timeout_s, s.serialize) == (130.0, 200.0, 600.0, True)
    assert s.loudnorm == DEFAULT_LOUDNORM
    assert s.target("phone").device is None


def test_numbers_empty_or_bad_are_their_default():
    s = Speech.from_env({"MEDIA_SPEECH_VOLUME": "", "MEDIA_SPEECH_LOCK_TIMEOUT_S": "soon",
                         "MEDIA_SPEECH_CODE_MAX_LINES": " 5 ", "MEDIA_SPEECH_DUCK_GRACE_S": "-2"})
    assert (s.volume, s.lock_timeout_s, s.code_max_lines, s.duck_grace_s) == (130.0, 600.0, 5, 0.0)


def test_switches():
    assert Speech.from_env({"MEDIA_SPEECH_SUMMARY": " 1 "}).summary
    assert not Speech.from_env({"MEDIA_SPEECH_SUMMARY": "yes"}).summary
    for off in ("0", "false", "NO"):
        assert not Speech.from_env({"MEDIA_SPEECH_SERIALIZE": off}).serialize
    assert Speech.from_env({"MEDIA_SPEECH_SERIALIZE": ""}).serialize


def test_set_empty_is_not_unset():
    """A target's device set empty is the default device, not "no setting";
    loudnorm set empty is no filter, not the default one."""
    s = Speech.from_env({"MEDIA_SPEECH_DEVICE_PHONE": "", "MEDIA_SPEECH_LOUDNORM": ""})
    assert s.target("phone").device == ""
    assert s.target("rooms").device is None
    assert s.loudnorm == ""


def test_per_target_names():
    s = Speech.from_env({"MEDIA_SPEECH_CLIP_SSH_PHONE_2": "p8a", "MEDIA_SPEECH_CLIP_SIGNED_PHONE_2": "1",
                         "MEDIA_SPEECH_PLAYOUT_MS_PHONE_2": "250", "MEDIA_SPEECH_DEVICE_VOICE_PHONE_2": "v"})
    t = s.target("phone-2")
    assert (t.clip_ssh, t.clip_signed, t.playout_ms, t.device_voice, t.device) == ("p8a", True, 250.0, "v", None)


def test_reads_the_environment_as_it_is_now(monkeypatch):
    monkeypatch.setenv("MEDIA_SPEECH_VOLUME", "90")
    assert settings.speech().volume == 90.0
    monkeypatch.setenv("MEDIA_SPEECH_VOLUME", "95")
    assert settings.speech().volume == 95.0


def test_use_skips_the_environment(monkeypatch):
    monkeypatch.setenv("MEDIA_SPEECH_VOLUME", "90")
    with settings.use(speech=Speech(volume=70.0)):
        assert settings.speech().volume == 70.0
        from agent_media_core.sinks import speech as sink
        assert sink._broker_default_volume() == 70.0
    assert settings.speech().volume == 90.0
