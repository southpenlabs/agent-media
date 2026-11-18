"""What the Chatterbox and Orpheus servers share: the HTTP endpoints, one
render at a time, the model loaded on the first /tts, and exit when idle.

A slow voice's model is large (Chatterbox about 3 GB in memory, Orpheus's
3B GGUF about 4 GB) and used only while one of its voices is chosen, so a
server holds it only while it is being used: it starts with nothing loaded
(/health and /voices answer at once), loads on the first /tts, and exits by
itself after an idle time with no request (`<ENGINE>_IDLE_S`, default 600;
0 never). The render client starts its unit again on the next reply
(docs/proposals/2026-11-14-voice-picker-providers.md, "Decided").

Endpoints:
  GET  /health   -> 200 "ok"
  GET  /voices   -> {"ok": true, "model": ..., "voices": [...]}
  POST /tts      -> audio/wav   body: {"text", "voice"?}

A reply is rendered sentence by sentence (both models stop after a fixed
number of tokens, about 40 seconds of speech) and joined with a short pause
into one WAV. Stdlib only; numpy is used for the WAV when it is there.
"""
from __future__ import annotations

import io
import json
import os
import re
import sys
import threading
import time
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable, Protocol, Sequence


def swallowed(e: BaseException, where: str) -> None:
    """A failure left behind on purpose, said once in the journal.

    The servers run in their own venv without agent-media-core, so this is
    a print rather than core's failures.swallowed."""
    print(f"{where}: {type(e).__name__}: {e}", file=sys.stderr, flush=True)


def env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name) or default)
    except ValueError:
        return default


def env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name) or default)
    except ValueError:
        return default


def data_dir(engine: str) -> str:
    """~/.local/share/agent-media/tts/<engine> (XDG_DATA_HOME honoured), as
    media-setup voices lays it out."""
    base = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    return os.path.join(base, "agent-media", "tts", engine)


_SENTENCE_END = re.compile(r"(?<=[.!?…])\s+|(?<=[.!?…][\"')\]”’])\s+")


def chunks(text: str, max_chars: int = 300) -> list[str]:
    """`text` cut at sentence ends into pieces of at most about `max_chars`;
    a sentence longer than that is cut at a comma or a space."""
    text = " ".join(text.split())
    if not text:
        return []
    pieces: list[str] = []
    for sentence in _SENTENCE_END.split(text):
        sentence = sentence.strip()
        while len(sentence) > max_chars:
            cut = max(sentence.rfind(", ", 0, max_chars), sentence.rfind("; ", 0, max_chars))
            cut = cut + 1 if cut > max_chars // 3 else sentence.rfind(" ", 0, max_chars)
            if cut <= 0:
                cut = max_chars
            pieces.append(sentence[:cut].strip())
            sentence = sentence[cut:].strip()
        if sentence:
            pieces.append(sentence)
    out: list[str] = []
    for p in pieces:   # short sentences together, so a model is called less often
        if out and len(out[-1]) + 1 + len(p) <= max_chars:
            out[-1] = f"{out[-1]} {p}"
        else:
            out.append(p)
    return out


def to_wav(samples, sr: int) -> bytes:
    """Float samples in [-1, 1] -> 16-bit mono WAV bytes."""
    try:
        import numpy as np
    except ImportError:  # the servers have numpy; only tests get here without it
        from array import array
        pcm = array("h", (int(max(-1.0, min(1.0, float(s))) * 32767.0) for s in samples))
        if sys.byteorder != "little":
            pcm.byteswap()
        data = pcm.tobytes()
    else:
        arr = np.clip(np.asarray(samples, dtype="float32").reshape(-1), -1.0, 1.0)
        data = (arr * 32767.0).astype("<i2").tobytes()
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(data)
    return buf.getvalue()


class Backend(Protocol):
    """One model: what the server around it calls."""

    model_name: str

    def voices(self) -> list[str]: ...

    def load(self) -> None: ...

    def synth(self, text: str, voice: str) -> tuple[Sequence[float], int]: ...

    def close(self) -> None: ...


class Busy(Exception):
    """The model stayed busy past the queue wait."""


class UnknownVoice(ValueError):
    pass


class Service:
    """The backend behind its one lock, loaded on first use, with the time
    of the last request for the idle exit. `clock` is time.monotonic unless
    a test gives its own."""

    def __init__(self, backend: Backend, *, default_voice: str, max_chars: int = 300,
                 pause_s: float = 0.25, queue_wait_s: float = 900.0,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.backend = backend
        self.default_voice = default_voice
        self.max_chars = max_chars
        self.pause_s = pause_s
        self.queue_wait_s = queue_wait_s
        self.clock = clock
        self.lock = threading.Lock()
        self.loaded = False
        self.active = 0          # requests in flight (a render, or waiting for one)
        self.last = clock()
        self._count = threading.Lock()

    def touch(self) -> None:
        with self._count:
            self.last = self.clock()

    def begin(self) -> None:
        with self._count:
            self.active += 1
            self.last = self.clock()

    def end(self) -> None:
        with self._count:
            self.active -= 1
            self.last = self.clock()

    def idle_for(self) -> float:
        """Seconds since the last request ended; 0 while one is in flight."""
        with self._count:
            return 0.0 if self.active else self.clock() - self.last

    def voices(self) -> list[str]:
        return self.backend.voices()

    def render(self, text: str, voice: str | None = None) -> bytes:
        voice = voice or self.default_voice
        if voice not in self.backend.voices():
            raise UnknownVoice(f"no voice {voice!r} (have: {', '.join(self.backend.voices())})")
        pieces = chunks(text, self.max_chars)
        if not pieces:
            raise ValueError("nothing to read")
        if not self.lock.acquire(timeout=self.queue_wait_s):
            raise Busy("busy")
        try:
            if not self.loaded:
                t0 = time.perf_counter()
                self.backend.load()
                self.loaded = True
                print(f"{self.backend.model_name}: loaded in {time.perf_counter() - t0:.1f} s",
                      file=sys.stderr, flush=True)
            out: list[float] = []
            sr = 24000
            for i, piece in enumerate(pieces):
                samples, sr = self.backend.synth(piece, voice)
                if i:
                    out.extend([0.0] * int(sr * self.pause_s))
                out.extend(float(s) for s in _flat(samples))
        finally:
            self.lock.release()
        return to_wav(out, sr)


def _flat(samples):
    if hasattr(samples, "reshape"):   # numpy array, any shape
        return samples.reshape(-1).tolist()
    return samples


def make_handler(service: Service):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *a):  # quiet; journald gets what we print
            pass

        def _send(self, code: int, body: bytes, ctype: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass  # the client gave up; nothing to tell it

        def do_GET(self):
            service.touch()
            path = self.path.split("?", 1)[0].rstrip("/")
            if path in ("/health", "/healthz"):
                self._send(200, b"ok", "text/plain")
            elif path == "/voices":
                try:
                    body = {"ok": True, "model": service.backend.model_name,
                            "loaded": service.loaded, "voices": service.voices()}
                except Exception as e:  # noqa: BLE001 — said in the answer
                    body = {"ok": False, "model": service.backend.model_name, "voices": [],
                            "error": str(e)}
                self._send(200 if body["ok"] else 500, json.dumps(body).encode(),
                           "application/json")
            else:
                self._send(404, b"not found", "text/plain")

        def do_POST(self):
            path = self.path.split("?", 1)[0].rstrip("/")
            if path not in ("/tts", "/v1/audio/speech"):
                self._send(404, b"not found", "text/plain")
                return
            service.begin()
            try:
                self._tts()
            finally:
                service.end()

        def _tts(self):
            try:
                length = int(self.headers.get("Content-Length", "0"))
                payload = json.loads(self.rfile.read(length) or b"{}")
                if not isinstance(payload, dict):
                    raise ValueError("body is not a JSON object")
            except Exception as e:  # noqa: BLE001 — said in the answer
                self._send(400, f"bad request: {e}".encode(), "text/plain")
                return
            text = str(payload.get("text") or payload.get("input") or "").strip()
            if not text:
                self._send(400, b"missing 'text'", "text/plain")
                return
            voice = str(payload.get("voice") or "") or None
            t0 = time.perf_counter()
            try:
                wav = service.render(text, voice)
            except UnknownVoice as e:
                self._send(400, str(e).encode(), "text/plain")
                return
            except Busy:
                self._send(503, b"busy", "text/plain")
                return
            except Exception as e:  # noqa: BLE001 — said in the answer and the journal
                swallowed(e, f"{service.backend.model_name} /tts")
                self._send(500, f"synth failed: {e}".encode(), "text/plain")
                return
            print(f"{service.backend.model_name}: {len(text)} chars in "
                  f"{time.perf_counter() - t0:.1f} s ({voice or service.default_voice})",
                  file=sys.stderr, flush=True)
            self._send(200, wav, "audio/wav")

    return Handler


def watch_idle(service: Service, server, idle_s: float, *, poll_s: float = 5.0,
               stop: threading.Event | None = None) -> threading.Thread | None:
    """Shut `server` down once no request has come for `idle_s` seconds
    (none with idle_s <= 0). The process then exits 0, which a unit with
    Restart=on-failure leaves stopped."""
    if idle_s <= 0:
        return None
    stop = stop or threading.Event()

    def run():
        while not stop.wait(poll_s):
            if service.idle_for() >= idle_s:
                print(f"{service.backend.model_name}: idle {idle_s:.0f} s, exiting "
                      "(started again on the next reply)", file=sys.stderr, flush=True)
                server.shutdown()
                return

    t = threading.Thread(target=run, name="idle-exit", daemon=True)
    t.start()
    return t


def serve(service: Service, host: str, port: int, idle_s: float, banner: str) -> int:
    """Run until idle, SIGTERM or ^C; the backend is closed on the way out."""
    import signal

    srv = ThreadingHTTPServer((host, port), make_handler(service))
    srv.daemon_threads = True

    def on_term(signum, frame):  # systemctl stop: shut down cleanly, child included
        threading.Thread(target=srv.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, on_term)
    watch_idle(service, srv, idle_s)
    print(f"{banner} listening on {host}:{port} (idle exit after "
          f"{idle_s:.0f} s; model loads on the first /tts)", file=sys.stderr, flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
        try:
            service.backend.close()
        except Exception as e:  # noqa: BLE001 — exiting anyway
            swallowed(e, f"{service.backend.model_name} close")
    return 0
