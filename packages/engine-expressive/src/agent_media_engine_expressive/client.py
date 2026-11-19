"""The render client both slow voices share: POST /tts, starting the
server's unit first when it is not running.

The servers exit by themselves when idle (their model is in memory only
while its voice is used), so a refused connection is the usual state, not a
fault: the client starts the systemd user unit, waits for /health, then
renders. Only for a server on this machine (a loopback base URL); one on
another host is that host's to start. On any failure it returns
(False, err) and core falls back as for every engine.
"""
from __future__ import annotations

import http.client
import json
import os
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


@dataclass(frozen=True)
class Engine:
    name: str              # chatterbox, orpheus
    default_url: str
    default_voice: str
    unit: str

    def _env(self, suffix: str) -> str:
        return os.environ.get(f"MEDIA_{self.name.upper()}_{suffix}") or ""

    def base_url(self) -> str:
        return (self._env("BASE_URL") or self.default_url).rstrip("/")

    def voice(self) -> str:
        return os.environ.get(f"MEDIA_RENDER_VOICE_{self.name.upper()}") or self.default_voice

    def seconds(self, suffix: str, default: float) -> float:
        try:
            return float(self._env(suffix) or default)
        except ValueError:
            return default


CHATTERBOX = Engine("chatterbox", "http://127.0.0.1:8882", "default",
                    "agent-media-chatterbox-tts.service")
ORPHEUS = Engine("orpheus", "http://127.0.0.1:8883", "tara",
                 "agent-media-orpheus-tts.service")


def _refused(e: BaseException) -> bool:
    """Nothing listening (or a server just exiting idle as we came)."""
    reason = e.reason if isinstance(e, urllib.error.URLError) else e
    return isinstance(reason, (ConnectionRefusedError, ConnectionResetError,
                               http.client.RemoteDisconnected))


def _is_local(url: str) -> bool:
    host = urllib.parse.urlparse(url).hostname or ""
    return host in ("127.0.0.1", "localhost", "::1") or host.startswith("127.")


def start_unit(unit: str) -> tuple[bool, str]:
    try:
        r = subprocess.run(["systemctl", "--user", "start", "--no-block", unit],
                           capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as e:
        return False, str(e)
    return r.returncode == 0, (r.stderr or r.stdout).strip()


def wait_health(base_url: str, wait_s: float, *, urlopen=urllib.request.urlopen,
                sleep: Callable[[float], None] = time.sleep,
                clock: Callable[[], float] = time.monotonic) -> bool:
    deadline = clock() + wait_s
    while True:
        try:
            with urlopen(f"{base_url}/health", timeout=3) as r:
                if r.status == 200:
                    return True
        except (OSError, http.client.HTTPException):
            pass  # not up yet: the loop is the wait
        if clock() >= deadline:
            return False
        sleep(0.5)


def render_with(engine: Engine, text: str, outfile: Path, *, voice: str | None = None,
                urlopen=urllib.request.urlopen,
                start: Callable[[str], tuple[bool, str]] = start_unit,
                sleep: Callable[[float], None] = time.sleep,
                clock: Callable[[], float] = time.monotonic) -> tuple[bool, str]:
    """Render `text` to a WAV at `outfile` through `engine`'s local server."""
    name = engine.name
    base = engine.base_url()
    body = json.dumps({"text": text, "voice": voice or engine.voice()}).encode()
    timeout = engine.seconds("TIMEOUT_S", 600)

    def post() -> bytes:
        req = urllib.request.Request(f"{base}/tts", data=body, method="POST",
                                     headers={"Content-Type": "application/json"})
        with urlopen(req, timeout=timeout) as resp:
            return resp.read()

    try:
        try:
            audio = post()
        except Exception as e:  # noqa: BLE001 — sorted below; anything else is the caller's fallback
            if not (_refused(e) and _is_local(base)):
                raise
            ok, why = start(engine.unit)
            if not ok:
                return False, f"{name}: not running, and starting {engine.unit} failed: {why}"
            if not wait_health(base, engine.seconds("START_WAIT_S", 60), urlopen=urlopen,
                               sleep=sleep, clock=clock):
                return False, (f"{name}: started {engine.unit}, no answer at {base}/health: "
                               f"journalctl --user -u {engine.unit}")
            audio = post()
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = e.read().decode(errors="replace")[:200]
        except Exception:  # noqa: BLE001 — the status is enough without it
            detail = ""
        return False, f"{name} http {e.code}: {detail}".rstrip(": ")
    except Exception as e:  # noqa: BLE001 — any transport error → the caller's fallback
        return False, f"{name} http: {e}"
    if not audio:
        return False, f"{name} returned empty audio"
    try:
        outfile.write_bytes(audio)
    except OSError as e:
        return False, f"{name} write failed: {e}"
    return True, ""
