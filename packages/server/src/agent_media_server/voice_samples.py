"""A short sample of each voice the server renders, and how fast it renders.

`GET /speech/voice/sample?voice=<name>` (the voice picker's Play button,
docs/proposals/2026-11-14-voice-picker-providers.md step 2): one sentence,
said once per voice in its own language, kept in
`<cache>/voice-samples/` as mp3, and timed: render seconds over clip
seconds is the voice's real-time factor on this server (`rtf`), kept in
`index.json` beside the clips and carried on every voice the picker lists.
A voice only the phone has (Google's) has no sample here.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import threading
import time
import wave
from pathlib import Path
from typing import Optional

#: What each sample says, by language; English for any other.
SENTENCES = {
    "en": "Oh, that's brilliant! It actually worked on the first try. "
          "I honestly didn't expect that at all.",
    "es": "¡Qué maravilla! Funcionó a la primera. La verdad, no me lo esperaba.",
    "fr": "C'est génial ! Ça a marché du premier coup. Honnêtement, je ne m'y attendais pas.",
    "de": "Das ist großartig! Es hat auf Anhieb geklappt. Ehrlich, damit hätte ich nicht gerechnet.",
    "it": "Fantastico! Ha funzionato al primo colpo. Sinceramente non me lo aspettavo.",
    "pt": "Que maravilha! Funcionou de primeira. Sinceramente, eu não esperava.",
    "ja": "すごい！一回目でうまくいきました。正直、まったく予想していませんでした。",
    "zh": "太棒了！第一次就成功了。说实话，我完全没想到。",
    "hi": "यह तो कमाल है! पहली बार में ही काम कर गया। सच कहूँ तो मुझे बिल्कुल उम्मीद नहीं थी।",
}
#: A voice renders live when its real-time factor is under this.
LIVE_RTF = 0.8

_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def folder() -> Path:
    from agent_media_core._paths import cache_dir

    return cache_dir() / "voice-samples"


def _index_path() -> Path:
    return folder() / "index.json"


def index() -> dict:
    try:
        data = json.loads(_index_path().read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _save_index(data: dict) -> None:
    path = _index_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f".tmp.{os.getpid()}.{threading.get_ident()}")
    tmp.write_text(json.dumps(data, indent=0, sort_keys=True))
    tmp.replace(path)


def sentence(locale: str) -> str:
    return SENTENCES.get((locale or "en").split("-")[0].lower(), SENTENCES["en"])


def _file_for(name: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", name)[:80]
    digest = hashlib.sha256(name.encode()).hexdigest()[:8]
    return folder() / f"{safe}-{digest}.mp3"


def sample_path(name: str) -> str:
    """The picker's `sample` for a voice: the path the app asks."""
    from urllib.parse import quote

    return f"/speech/voice/sample?voice={quote(name, safe='')}"


def _duration_s(path: Path) -> Optional[float]:
    try:
        with wave.open(str(path), "rb") as w:
            return w.getnframes() / float(w.getframerate())
    except Exception:  # noqa: BLE001 — not a WAV: ask ffprobe
        pass
    probe = shutil.which("ffprobe")
    if not probe:
        return None
    try:
        out = subprocess.run([probe, "-v", "error", "-show_entries", "format=duration",
                              "-of", "default=nw=1:nk=1", str(path)],
                             capture_output=True, text=True, timeout=20)
        return float(out.stdout.strip())
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def _to_mp3(src: Path, dst: Path) -> bool:
    """`src` as an mp3 at `dst`; a clip that is already one is moved."""
    with open(src, "rb") as f:
        head = f.read(4)
    if head[:3] == b"ID3" or (len(head) > 1 and head[0] == 0xFF and head[1] & 0xE0 == 0xE0):
        src.replace(dst)
        return True
    ff = shutil.which("ffmpeg")
    if not ff:
        return False
    r = subprocess.run([ff, "-y", "-v", "error", "-i", str(src), "-ac", "1", "-b:a", "48k",
                        "-f", "mp3", str(dst)], capture_output=True, timeout=60)
    src.unlink(missing_ok=True)
    return r.returncode == 0 and dst.exists() and dst.stat().st_size > 0


def render(voice: dict) -> tuple[bool, dict]:
    """The sample of `voice` (a catalog entry), rendered and timed the first
    time, kept after. (True, {"file", "size", "type"}) or (False, error)."""
    from agent_media_core.render import device, render_text

    name = voice["name"]
    routed = device.engine_voice(name) if "server" in (voice.get("where") or []) else None
    if not routed:
        return False, {"status": 404, "error": f"{voice.get('label') or name} has no sample here"}
    out = _file_for(name)
    with _locks_guard:
        lock = _locks.setdefault(name, threading.Lock())
    with lock:
        if not (out.exists() and out.stat().st_size > 0):
            out.parent.mkdir(parents=True, exist_ok=True)
            part = out.with_name(f".{out.stem}.{os.getpid()}.part")
            engine, engine_voice = routed
            started = time.monotonic()
            done, err = render_text(sentence(voice.get("locale") or ""), part, engine=engine,
                                    voice=engine_voice, fallback_to_edge=False)
            took = time.monotonic() - started
            if not done or not part.exists() or part.stat().st_size == 0:
                part.unlink(missing_ok=True)
                return False, {"status": 502, "error": f"could not render: {err or 'empty'}"}
            length = _duration_s(part)
            if not _to_mp3(part, out):
                part.unlink(missing_ok=True)
                return False, {"status": 500, "error": "could not make an mp3 (ffmpeg?)"}
            if length:
                data = index()
                data[name] = {"rtf": round(took / length, 2), "seconds": round(length, 2),
                              "measured": int(time.time())}
                try:
                    _save_index(data)
                except OSError:
                    pass
    return True, {"file": str(out), "size": out.stat().st_size, "type": "audio/mpeg"}


def annotate(voices: list) -> None:
    """Give each voice the server renders its `sample` path, and the `rtf`
    and `live` measured here when known (in place)."""
    known = index()
    for v in voices:
        if "server" not in (v.get("where") or []):
            continue
        v["sample"] = sample_path(v["name"])
        got = known.get(v["name"]) or {}
        if isinstance(got.get("rtf"), (int, float)):
            v["rtf"] = got["rtf"]
            v["live"] = got["rtf"] < LIVE_RTF
