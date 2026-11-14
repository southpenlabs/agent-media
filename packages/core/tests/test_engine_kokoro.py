"""engine-kokoro: the render engine's request and kokoro-tts-server's endpoints.

No model, no network beyond loopback: the server is driven with a fake
kokoro-onnx object on an ephemeral 127.0.0.1 port, and the render engine's
HTTP call is captured by replacing urlopen.
"""
from __future__ import annotations

import io
import json
import sys
import threading
import urllib.request
import wave
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

try:  # CI installs core, server and visual only: read the package from the tree
    import agent_media_engine_kokoro  # noqa: F401
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "engine-kokoro" / "src"))

import agent_media_engine_kokoro as engine
from agent_media_engine_kokoro import server


class FakeKokoro:
    def __init__(self):
        self.calls = []

    def get_voices(self):
        return ["bf_emma", "af_heart", "af_alloy"]

    def create(self, text, *, voice, speed, lang):
        self.calls.append((text, voice, speed, lang))
        return [0.0, 0.5, -0.5, 2.0], 24000


@pytest.fixture
def srv(monkeypatch):
    monkeypatch.delenv("KOKORO_LANG", raising=False)
    monkeypatch.delenv("KOKORO_VOICE", raising=False)
    fake = FakeKokoro()
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.make_handler(server.Engine(fake)))
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}", fake
    httpd.shutdown()
    httpd.server_close()


def _post(url, body):
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=5) as r:
        return r.status, r.headers.get("Content-Type"), r.read()


@pytest.mark.parametrize("voice,lang", [
    ("af_heart", "en-us"), ("bf_emma", "en-gb"), ("bm_george", "en-gb"),
    ("ef_dora", "es"), ("ff_siwis", "fr-fr"), ("hf_alpha", "hi"),
    ("if_sara", "it"), ("jf_alpha", "ja"), ("pm_alex", "pt-br"),
    ("zf_xiaobei", "cmn"),
])
def test_lang_from_the_voice_prefix(voice, lang, monkeypatch):
    monkeypatch.delenv("KOKORO_LANG", raising=False)
    assert server.lang_for(voice) == lang


def test_unknown_prefix_falls_back_to_kokoro_lang(monkeypatch):
    monkeypatch.delenv("KOKORO_LANG", raising=False)
    assert server.lang_for("custom") == "en-us"
    assert server.lang_for("xf_who") == "en-us"
    monkeypatch.setenv("KOKORO_LANG", "en-gb")
    assert server.lang_for("custom") == "en-gb"


def test_defaults_bind_loopback_and_new_model_dir(monkeypatch, tmp_path):
    monkeypatch.delenv("KOKORO_MODEL_DIR", raising=False)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    assert server.model_dir() == str(tmp_path / "agent-media" / "tts" / "kokoro" / "models")
    monkeypatch.delenv("KOKORO_VOICE", raising=False)
    assert server.default_voice() == "af_alloy"


def test_health_and_voices(srv):
    base, _ = srv
    with urllib.request.urlopen(base + "/health", timeout=5) as r:
        assert r.read() == b"ok"
    with urllib.request.urlopen(base + "/voices", timeout=5) as r:
        body = json.loads(r.read())
    assert body == {"ok": True, "model": "kokoro-v1.0",
                    "voices": ["af_alloy", "af_heart", "bf_emma"]}


def test_tts_reads_a_british_voice_in_british_english(srv):
    base, fake = srv
    status, ctype, wav = _post(base + "/tts", {"text": "Hello there", "voice": "bf_emma"})
    assert (status, ctype) == (200, "audio/wav")
    assert fake.calls == [("Hello there", "bf_emma", 1.0, "en-gb")]
    with wave.open(io.BytesIO(wav)) as w:
        assert (w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()) == (1, 2, 24000, 4)


def test_explicit_lang_wins_and_default_voice_is_alloy(srv):
    base, fake = srv
    _post(base + "/tts", {"text": "x", "voice": "bf_emma", "lang": "en-us", "speed": 1.2})
    _post(base + "/v1/audio/speech", {"input": "y"})
    assert fake.calls == [("x", "bf_emma", 1.2, "en-us"), ("y", "af_alloy", 1.0, "en-us")]


def test_missing_text_is_400(srv):
    base, _ = srv
    with pytest.raises(urllib.error.HTTPError) as e:
        _post(base + "/tts", {"voice": "af_heart"})
    assert e.value.code == 400


class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _capture(monkeypatch):
    sent = {}

    def fake_urlopen(req, timeout):
        sent["body"] = json.loads(req.data)
        sent["url"] = req.full_url
        return _Resp(b"RIFFfake")
    monkeypatch.setattr(engine.urllib.request, "urlopen", fake_urlopen)
    return sent


def test_render_sends_no_lang_unless_set(monkeypatch, tmp_path):
    for k in ("MEDIA_KOKORO_LANG", "MEDIA_KOKORO_BASE_URL", "MEDIA_RENDER_VOICE_KOKORO"):
        monkeypatch.delenv(k, raising=False)
    sent = _capture(monkeypatch)
    ok, err = engine.render("Hi", tmp_path / "a.wav", voice="bf_emma")
    assert (ok, err) == (True, "")
    assert "lang" not in sent["body"]
    assert sent["body"]["voice"] == "bf_emma"
    assert sent["url"] == "http://127.0.0.1:8880/tts"


def test_render_sends_lang_when_set(monkeypatch, tmp_path):
    monkeypatch.setenv("MEDIA_KOKORO_LANG", "en-gb")
    sent = _capture(monkeypatch)
    engine.render("Hi", tmp_path / "a.wav", voice="af_heart")
    assert sent["body"]["lang"] == "en-gb"
