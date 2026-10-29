"""Speech-to-text (agent_media_core.transcribe): whichever service has a key,
in order; the two kinds of request; the cache. No service is ever reached:
`_post` is replaced, and every key variable is cleared first."""

from __future__ import annotations

import json

import pytest

from agent_media_core import transcribe as stt

KEYS = ("MEDIA_STT", "MEDIA_STT_URL", "MEDIA_STT_KEY", "MEDIA_STT_PROVIDER", "MEDIA_STT_MODEL",
        "GROQ_API_KEY", "OPENAI_API_KEY", "OPENAI_BASE_URL", "OPENROUTER_API_KEY",
        "GEMINI_API_KEY", "GOOGLE_API_KEY")


@pytest.fixture(autouse=True)
def clean(tmp_path, monkeypatch):
    for k in KEYS:
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("MEDIA_STT_KEY_FILE", str(tmp_path / "litellm.env"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    sent = []

    def post(url, data, headers):
        sent.append((url, data, headers))
        if url.endswith("/audio/transcriptions"):
            return {"text": "  pick up   some milk "}
        return {"choices": [{"message": {"content": "pick up some milk"}}]}

    monkeypatch.setattr(stt, "_post", post)
    return sent


def test_none_set_up():
    assert stt.provider() is None
    assert stt.status() == {"provider": None, "model": None, "available": []}
    with pytest.raises(stt.SttError):
        stt.transcribe(__file__)


def test_the_first_with_a_key_and_a_choice(tmp_path, monkeypatch):
    (tmp_path / "litellm.env").write_text("OPENROUTER_API_KEY='or-key'\n")
    assert stt.provider()["name"] == "openrouter"     # from the gateway's file
    monkeypatch.setenv("GROQ_API_KEY", "g")
    assert stt.provider()["name"] == "groq"
    assert stt.status()["available"] == ["groq", "openrouter"]
    monkeypatch.setenv("MEDIA_STT_PROVIDER", "openrouter")
    monkeypatch.setenv("MEDIA_STT_MODEL", "google/gemini-2.5-pro")
    p = stt.provider()
    assert (p["name"], p["key"], p["model"]) == ("openrouter", "or-key", "google/gemini-2.5-pro")
    monkeypatch.setenv("MEDIA_STT_URL", "http://box:9000/v1/")
    monkeypatch.delenv("MEDIA_STT_PROVIDER")
    p = stt.provider()
    assert (p["name"], p["base"], p["key"]) == ("custom", "http://box:9000/v1", "")
    monkeypatch.setenv("MEDIA_STT", "0")
    assert stt.provider() is None


def test_an_openai_key_for_a_gateway_does_not_count(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "k")
    monkeypatch.setenv("OPENAI_BASE_URL", "http://red5:4000/v1")
    assert stt.provider() is None
    monkeypatch.setenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
    assert stt.provider()["name"] == "openai"


def test_a_transcription_service_and_the_cache(tmp_path, monkeypatch, clean):
    monkeypatch.setenv("GROQ_API_KEY", "g")
    monkeypatch.setattr(stt.shutil, "which", lambda _: None)   # sent as it is
    clip = tmp_path / "voice-clip-1.ogg"
    clip.write_bytes(b"OggS fake")
    assert stt.transcribe(clip, language="en") == "pick up some milk"
    (url, data, headers), = clean
    assert url == "https://api.groq.com/openai/v1/audio/transcriptions"
    assert headers["Authorization"] == "Bearer g"
    assert b'name="model"\r\n\r\nwhisper-large-v3-turbo' in data
    assert b'name="language"\r\n\r\nen' in data and b'filename="clip.ogg"' in data
    assert b"OggS fake" in data
    # The same clip again: from the cache, nothing sent.
    assert stt.transcribe(clip) == "pick up some milk" and len(clean) == 1


def test_a_model_that_hears(tmp_path, monkeypatch, clean):
    monkeypatch.setenv("OPENROUTER_API_KEY", "or")
    monkeypatch.setattr(stt.shutil, "which", lambda _: None)
    clip = tmp_path / "voice-clip-2.webm"
    clip.write_bytes(b"webm fake")
    assert stt.transcribe(clip) == "pick up some milk"
    (url, data, headers), = clean
    assert url == "https://openrouter.ai/api/v1/chat/completions"
    body = json.loads(data)
    assert body["model"] == "google/gemini-2.5-flash" and body["temperature"] == 0
    text, audio = body["messages"][0]["content"]
    assert text["type"] == "text" and audio["input_audio"]["format"] == "webm"


def test_a_clip_too_big(tmp_path, monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "g")
    monkeypatch.setattr(stt, "MAX_BYTES", 4)
    clip = tmp_path / "big.ogg"
    clip.write_bytes(b"12345")
    with pytest.raises(stt.SttError, match="too long"):
        stt.transcribe(clip)
