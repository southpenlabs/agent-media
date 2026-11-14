"""kokoro-tts-server: a small local Kokoro TTS HTTP server for agent-media.

Ported from the hand-made server that ran on red5 (`~/kokoro-tts/server.py`),
so `media-setup voices kokoro` (and `sasonica install --local-voice kokoro`)
can install it as a service. It loads the kokoro-onnx model once at start and
serves WAV synthesis. Inference is serialized with a lock: onnxruntime already
saturates every core per call, so concurrent requests would only oversubscribe
the CPU.

Endpoints:
  GET  /health            -> 200 "ok"
  GET  /voices            -> {"ok": true, "model": "kokoro-v1.0", "voices": [...]}
  POST /tts               -> audio/wav   body: {"text","voice"?,"speed"?,"lang"?}
  POST /v1/audio/speech   -> audio/wav   OpenAI-ish: {"input","voice"?,"speed"?}

A request without "lang" is read in the voice's own language, from the first
letter of its name (`bf_emma` is British English, `ef_dora` Spanish); a voice
with no known prefix gets KOKORO_LANG.

Config (env):
  KOKORO_MODEL_DIR  dir holding kokoro-v1.0.onnx + voices-v1.0.bin
                    (default ~/.local/share/agent-media/tts/kokoro/models)
  KOKORO_VOICE      default voice            (default af_alloy)
  KOKORO_LANG       language when the voice does not say (default en-us)
  KOKORO_BIND       bind host                (default 127.0.0.1)
  KOKORO_PORT       bind port                (default 8880)

Bound to loopback by default: the render engine runs on the same machine
(docs/decisions/2026-10-18-off-the-tailnet.md). Needs the `[server]` extra
(kokoro-onnx, numpy); the render engine beside it needs neither. This module
imports nothing from the package, so it also runs as a copied single file.
"""
from __future__ import annotations

import io
import json
import os
import sys
import threading
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

MODEL_NAME = "kokoro-v1.0"
MODEL_FILE = "kokoro-v1.0.onnx"
VOICES_FILE = "voices-v1.0.bin"
DEFAULT_PORT = 8880

#: Kokoro's voice-name prefix -> the espeak-ng language kokoro-onnx phonemizes
#: with (VOICES.md of hexgrad/Kokoro-82M: a American English, b British
#: English, e Spanish, f French, h Hindi, i Italian, j Japanese, p Brazilian
#: Portuguese, z Mandarin).
LANG_BY_PREFIX = {
    "a": "en-us", "b": "en-gb", "e": "es", "f": "fr-fr", "h": "hi",
    "i": "it", "j": "ja", "p": "pt-br", "z": "cmn",
}


def model_dir() -> str:
    default = os.path.join(
        os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share"),
        "agent-media", "tts", "kokoro", "models")
    return os.environ.get("KOKORO_MODEL_DIR") or default


def default_voice() -> str:
    return os.environ.get("KOKORO_VOICE") or "af_alloy"


def default_lang() -> str:
    return os.environ.get("KOKORO_LANG") or "en-us"


def lang_for(voice: str, fallback: str | None = None) -> str:
    """The language `voice` is spoken in, from its prefix letter."""
    v = (voice or "").strip()
    if len(v) >= 2 and v[1] in "fm_" and v[0] in LANG_BY_PREFIX:
        return LANG_BY_PREFIX[v[0]]
    return fallback or default_lang()


def to_wav(samples, sr: int) -> bytes:
    """Float samples in [-1, 1] -> 16-bit mono WAV bytes."""
    try:
        import numpy as np
    except ImportError:  # numpy is the server's; only tests get here without it
        from array import array
        pcm = array("h", (int(max(-1.0, min(1.0, float(s))) * 32767.0) for s in samples))
        if sys.byteorder != "little":
            pcm.byteswap()
        data = pcm.tobytes()
    else:
        arr = np.clip(np.asarray(samples, dtype="float32"), -1.0, 1.0)
        data = (arr * 32767.0).astype("<i2").tobytes()
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(data)
    return buf.getvalue()


class Engine:
    """The loaded model, behind the one lock every synthesis takes."""

    def __init__(self, kokoro) -> None:
        self.kokoro = kokoro
        self.lock = threading.Lock()

    @classmethod
    def load(cls, directory: str) -> "Engine":
        from kokoro_onnx import Kokoro

        return cls(Kokoro(os.path.join(directory, MODEL_FILE),
                          os.path.join(directory, VOICES_FILE)))

    def voices(self) -> list[str]:
        return sorted(self.kokoro.get_voices())

    def synth(self, text: str, voice: str, speed: float, lang: str) -> bytes:
        with self.lock:
            samples, sr = self.kokoro.create(text, voice=voice, speed=speed, lang=lang)
        return to_wav(samples, sr)


def make_handler(engine: Engine):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *a):  # quiet; journald gets what we print
            pass

        def _send(self, code: int, body: bytes, ctype: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            path = self.path.split("?", 1)[0].rstrip("/")
            if path in ("/health", "/healthz"):
                self._send(200, b"ok", "text/plain")
            elif path == "/voices":
                try:
                    body = {"ok": True, "model": MODEL_NAME, "voices": engine.voices()}
                except Exception as e:  # noqa: BLE001
                    body = {"ok": False, "model": MODEL_NAME, "voices": [], "error": str(e)}
                self._send(200 if body["ok"] else 500, json.dumps(body).encode(),
                           "application/json")
            else:
                self._send(404, b"not found", "text/plain")

        def do_POST(self):
            path = self.path.split("?", 1)[0].rstrip("/")
            if path not in ("/tts", "/v1/audio/speech"):
                self._send(404, b"not found", "text/plain")
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                payload = json.loads(self.rfile.read(length) or b"{}")
                if not isinstance(payload, dict):
                    raise ValueError("body is not a JSON object")
            except Exception as e:  # noqa: BLE001
                self._send(400, f"bad request: {e}".encode(), "text/plain")
                return
            text = str(payload.get("text") or payload.get("input") or "").strip()
            if not text:
                self._send(400, b"missing 'text'", "text/plain")
                return
            voice = payload.get("voice") or default_voice()
            lang = payload.get("lang") or lang_for(voice)
            try:
                speed = float(payload.get("speed", 1.0))
            except (TypeError, ValueError):
                speed = 1.0
            try:
                wav = engine.synth(text, voice, speed, lang)
            except Exception as e:  # noqa: BLE001
                self._send(500, f"synth failed: {e}".encode(), "text/plain")
                return
            self._send(200, wav, "audio/wav")

    return Handler


def main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(prog="kokoro-tts-server",
                                 description="Local Kokoro TTS over HTTP (WAV).")
    ap.add_argument("--host", default=os.environ.get("KOKORO_BIND") or "127.0.0.1")
    ap.add_argument("--port", type=int,
                    default=int(os.environ.get("KOKORO_PORT") or DEFAULT_PORT))
    ap.add_argument("--model-dir", default=model_dir())
    a = ap.parse_args(argv)
    try:
        engine = Engine.load(a.model_dir)
    except ImportError as e:
        print(f"kokoro-tts-server: {e} — install the [server] extra "
              "(pip install 'agent-media-engine-kokoro[server]')", file=sys.stderr)
        return 2
    except Exception as e:  # noqa: BLE001
        print(f"kokoro-tts-server: could not load the model from {a.model_dir}: {e}",
              file=sys.stderr)
        return 1
    srv = ThreadingHTTPServer((a.host, a.port), make_handler(engine))
    print(f"kokoro-tts listening on {a.host}:{a.port} "
          f"(voice={default_voice()} model_dir={a.model_dir})",
          file=sys.stderr, flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
