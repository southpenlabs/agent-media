"""`GET|POST /speech/stt` (server-contract.md §6.36a): the app's Settings →
Microphone → Dictation chooses this host's speech-to-text and pastes its key,
kept here in `<state_dir>/stt.json` (0600) and never answered back (David,
3 Nov 2026: "I'd probably prefer it to come from the settings")."""

from __future__ import annotations

import json
import os
import stat

import pytest

from agent_media_server import app

from test_contract import AUTH, call, server, signed_in, typed  # noqa: F401

KEYS = ("MEDIA_STT", "MEDIA_STT_URL", "MEDIA_STT_KEY", "MEDIA_STT_PROVIDER", "MEDIA_STT_MODEL",
        "GROQ_API_KEY", "OPENAI_API_KEY", "OPENAI_BASE_URL", "OPENROUTER_API_KEY",
        "GEMINI_API_KEY", "GOOGLE_API_KEY")


@pytest.fixture(autouse=True)
def no_keys(monkeypatch, tmp_path):
    # conftest turns speech-to-text off (MEDIA_STT=0); here it is on, with no
    # key from this machine's environment or its gateway's file.
    for k in KEYS:
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("MEDIA_STT_KEY_FILE", str(tmp_path / "no-litellm.env"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))


def _get(server):
    res, obj = call(server, "GET", "/speech/stt", headers=AUTH)
    assert res.status == 200, obj
    return obj


def _row(obj, name):
    return next(c for c in obj["choices"] if c["name"] == name)


def test_nothing_set_up(server, signed_in):
    obj = _get(server)
    assert obj == {"ok": True, "provider": None, "chosen": "auto", "model": None, "choices": [
        {"name": "groq", "label": "Groq", "model": "whisper-large-v3-turbo",
         "has_key": False, "from": None},
        {"name": "openai", "label": "OpenAI", "model": "gpt-4o-mini-transcribe",
         "has_key": False, "from": None},
        {"name": "openrouter", "label": "OpenRouter (Gemini)", "model": "google/gemini-2.5-flash",
         "has_key": False, "from": None},
        {"name": "gemini", "label": "Google Gemini", "model": "gemini-2.5-flash",
         "has_key": False, "from": None}]}
    assert "/speech/stt" in app.CORS_PATHS


def test_custom_only_with_its_url(server, signed_in, monkeypatch):
    monkeypatch.setenv("MEDIA_STT_URL", "http://box:9000/v1")
    obj = _get(server)
    assert obj["choices"][0] == {"name": "custom", "label": "Your own server",
                                 "model": "whisper-1", "has_key": True, "from": "env"}
    assert obj["provider"] == "custom"


def test_a_key_is_kept_and_never_answered(server, signed_in, monkeypatch, tmp_path):
    monkeypatch.setenv("GROQ_API_KEY", "g-env")
    assert _row(_get(server), "groq")["from"] == "env"
    res, obj = call(server, "POST", "/speech/stt",
                    body={"chosen": "openai", "key": "sk-secret-1"}, headers=AUTH)
    assert res.status == 200, obj
    assert (obj["provider"], obj["chosen"], obj["model"]) == ("openai", "openai",
                                                             "gpt-4o-mini-transcribe")
    assert _row(obj, "openai") == {"name": "openai", "label": "OpenAI",
                                   "model": "gpt-4o-mini-transcribe",
                                   "has_key": True, "from": "settings"}
    got = _get(server)
    assert got == obj
    assert "sk-secret-1" not in json.dumps(got)
    path = tmp_path / "state" / "agent-media" / "stt.json"
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    assert json.loads(path.read_text())["keys"] == {"openai": "sk-secret-1"}


def test_a_key_for_a_provider_given_alongside(server, signed_in):
    res, obj = call(server, "POST", "/speech/stt",
                    body={"provider": "groq", "key": "g-1", "model": "whisper-large-v3"},
                    headers=AUTH)
    assert res.status == 200, obj
    assert (obj["provider"], obj["chosen"], obj["model"]) == ("groq", "auto", "whisper-large-v3")
    _, obj = call(server, "POST", "/speech/stt", body={"provider": "groq", "key": ""},
                  headers=AUTH)
    assert obj["provider"] is None and not _row(obj, "groq")["has_key"]


def test_chosen_without_a_key_is_none_then_auto_again(server, signed_in, monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "g-env")
    res, obj = call(server, "POST", "/speech/stt", body={"chosen": "gemini"}, headers=AUTH)
    assert res.status == 200, obj
    assert (obj["provider"], obj["chosen"], obj["model"]) == (None, "gemini", None)
    _, obj = call(server, "POST", "/speech/stt", body={"chosen": "auto"}, headers=AUTH)
    assert (obj["provider"], obj["chosen"]) == ("groq", "auto")


@pytest.mark.parametrize("body", [{"chosen": "whisperer"}, {"provider": "nope", "key": "k"},
                                  {"key": "k"}, {}])
def test_a_bad_request_is_a_400(server, signed_in, body, tmp_path):
    res, obj = call(server, "POST", "/speech/stt", body=body, headers=AUTH)
    assert res.status == 400 and obj["ok"] is False and obj["error"]
    assert not (tmp_path / "state" / "agent-media" / "stt.json").exists()


def test_signed_out_is_turned_away(server):
    res, _ = call(server, "GET", "/speech/stt")
    assert res.status in (401, 403)
