"""Free or paid speech-to-text (stt_settings, transcribe.cost): a server on
this machine or the home network is free, anything else is paid by the call,
and the server pays only for whom MEDIA_STT_PAID_FOR says (David, 18 Nov
2026: "that can cost money if it's not installed locally", "it could be an
option for a paid tier")."""

from __future__ import annotations

import io

import pytest

from agent_media_core import transcribe
from agent_media_server import auth, stt_settings, voice_clips

from test_contract import AUTH, call, server, signed_in, typed  # noqa: F401
from test_stt_settings import no_keys  # noqa: F401  (its autouse scrub)

MEMBER = {"id": "d_member", "name": "pine note", "role": "member"}


@pytest.mark.parametrize("base,want", [
    ("http://127.0.0.1:52625/v1", "free"), ("http://localhost:9000/v1", "free"),
    ("http://192.168.0.5:9000/v1", "free"), ("http://100.94.14.59:9000/v1", "free"),
    ("http://red2:9000/v1", "free"), ("https://box.tail1234.ts.net/v1", "free"),
    ("http://whisper.lan/v1", "free"), ("https://stt.example.com/v1", "paid"),
    ("https://8.8.8.8/v1", "paid")])
def test_a_custom_server_is_free_on_this_network(monkeypatch, base, want):
    monkeypatch.setenv("MEDIA_STT_URL", base)
    assert transcribe.cost(transcribe.provider()) == want


def test_the_cost_said_outright_wins(monkeypatch):
    monkeypatch.setenv("MEDIA_STT_URL", "https://stt.example.com/v1")
    monkeypatch.setenv("MEDIA_STT_COST", "free")
    assert transcribe.cost(transcribe.provider()) == "free"
    monkeypatch.setenv("MEDIA_STT_URL", "http://127.0.0.1:1/v1")
    monkeypatch.setenv("MEDIA_STT_COST", "paid")
    assert transcribe.cost(transcribe.provider()) == "paid"


def test_a_cloud_service_is_paid(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "fake")
    assert transcribe.cost(transcribe.provider()) == "paid"
    assert transcribe.free_provider() is None
    assert transcribe.cost(None) is None


@pytest.mark.parametrize("paid_for,role,want", [
    ("", "admin", True), ("", "member", False), ("", "guest", False),
    ("everyone", "member", True), ("nobody", "admin", False), ("bogus", "admin", True)])
def test_who_the_server_pays_for(monkeypatch, paid_for, role, want):
    monkeypatch.setenv("MEDIA_STT_PAID_FOR", paid_for)
    assert stt_settings.covered({"device": "d", "role": role}) is want


def test_the_answer_says_cost_and_whether_you_may_dictate(server, signed_in, monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "fake")
    res, obj = call(server, "GET", "/speech/stt", headers=AUTH)
    assert res.status == 200
    assert (obj["cost"], obj["free"], obj["covered"], obj["paid_for"], obj["dictate"]) == \
        ("paid", False, True, "admins", True)
    monkeypatch.setattr(auth, "_device", lambda b: MEMBER)
    res, obj = call(server, "GET", "/speech/stt", headers=AUTH)
    assert (obj["covered"], obj["dictate"]) == (False, False)
    monkeypatch.setenv("MEDIA_STT_URL", "http://127.0.0.1:1/v1")
    res, obj = call(server, "GET", "/speech/stt", headers=AUTH)
    assert (obj["cost"], obj["free"], obj["dictate"]) == ("free", True, True)


def _dictate(monkeypatch, device):
    monkeypatch.setattr(auth, "_device", lambda b: device)
    return voice_clips.session_transcribe_audio(io.BytesIO(b"OggS" + b"\0" * 60), 64,
                                                "audio/ogg", "tok")


def test_a_member_is_refused_a_paid_service(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "fake")
    monkeypatch.setattr(transcribe, "transcribe", lambda *a, **k: pytest.fail("paid for"))
    ok, detail = _dictate(monkeypatch, MEMBER)
    assert not ok and detail["status"] == 402 and detail["code"] == "paid_plan"


def test_a_member_gets_the_free_service_only(monkeypatch):
    monkeypatch.setenv("MEDIA_STT_URL", "http://127.0.0.1:1/v1")
    monkeypatch.setenv("GROQ_API_KEY", "fake")
    seen = {}
    monkeypatch.setattr(transcribe, "transcribe",
                        lambda clip, language="", free_only=False: seen.update(f=free_only) or "hi")
    ok, detail = _dictate(monkeypatch, MEMBER)
    assert ok and detail["text"] == "hi" and detail["provider"] == "custom"
    assert seen == {"f": True}
    ok, detail = _dictate(monkeypatch, {"id": "d_admin", "name": "phone"})
    assert ok and seen == {"f": False}  # not asked: the default


def test_free_only_skips_the_paid_ones(monkeypatch, tmp_path):
    monkeypatch.setenv("GROQ_API_KEY", "fake")
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    clip = tmp_path / "c.ogg"
    clip.write_bytes(b"x")
    with pytest.raises(transcribe.SttError, match="no free"):
        transcribe.transcribe(clip, free_only=True)
