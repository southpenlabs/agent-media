"""chatterbox-tts-server: Resemble AI's Chatterbox (MIT) over HTTP for agent-media.

A slow, expressive local voice: about 4x slower than speech on red2's CPU,
so a reply in it is rendered whole before it plays (the voice is `live:
false` in the catalog). Voices are "default" (Chatterbox's own) and one per
reference clip in the voices dir, named by its file stem: natasha.wav is
"natasha", a clone of whoever speaks in it. `media-setup voices chatterbox
--clone NAME=AUDIO` makes such a clip (24 kHz mono WAV). Chatterbox marks
its audio with Resemble's Perth watermark, inaudible.

The model loads on the first /tts, not at start, and the server exits after
CHATTERBOX_IDLE_S with no request (see _server.py). Its weights
(ResembleAI/chatterbox) come from Hugging Face into the HF cache on the
first load (about 3 GB), not at install.

Config (env):
  CHATTERBOX_VOICES_DIR  reference clips (default
                         ~/.local/share/agent-media/tts/chatterbox/voices)
  CHATTERBOX_VOICE       default voice              (default "default")
  CHATTERBOX_BIND        bind host                  (default 127.0.0.1)
  CHATTERBOX_PORT        bind port                  (default 8882)
  CHATTERBOX_THREADS     torch threads for a render (default 4; 0 = torch's)
  CHATTERBOX_IDLE_S      exit after this long idle  (default 600; 0 never)
  CHATTERBOX_EXAGGERATION  emotion, 0.25-2           (default 0.5)
  CHATTERBOX_CFG_WEIGHT    pace/guidance, 0-1        (default 0.5)

Needs the [chatterbox] extra (chatterbox-tts 0.1.7, which wants
setuptools<81, and torch). The render client beside it needs neither.
"""
from __future__ import annotations

import os
import sys

from . import _server

MODEL_NAME = "chatterbox"
DEFAULT_PORT = 8882
DEFAULT_VOICE = "default"
CLIP_SUFFIX = ".wav"


def voices_dir() -> str:
    return os.environ.get("CHATTERBOX_VOICES_DIR") or os.path.join(
        _server.data_dir("chatterbox"), "voices")


def list_voices(directory: str | None = None) -> list[str]:
    """"default" first, then a voice per <name>.wav in the voices dir."""
    d = directory or voices_dir()
    try:
        names = sorted(f[: -len(CLIP_SUFFIX)] for f in os.listdir(d)
                       if f.endswith(CLIP_SUFFIX) and not f.startswith(".")
                       and f[: -len(CLIP_SUFFIX)] != DEFAULT_VOICE)
    except OSError:
        names = []
    return [DEFAULT_VOICE, *names]


def threads() -> int:
    return max(0, _server.env_int("CHATTERBOX_THREADS", 4))


class ChatterboxBackend:
    model_name = MODEL_NAME

    def __init__(self, directory: str | None = None) -> None:
        self.directory = directory or voices_dir()
        self.model = None
        self.default_conds = None
        self.conds: dict[str, tuple[float, object]] = {}   # voice -> (clip mtime, conds)

    def voices(self) -> list[str]:
        return list_voices(self.directory)

    def load(self) -> None:
        import torch
        from chatterbox.tts import ChatterboxTTS

        # torch takes every core by default; a few render nearly as fast and
        # leave the box usable (as KOKORO_THREADS, 18 Nov 2026).
        if threads():
            torch.set_num_threads(threads())
        self.model = ChatterboxTTS.from_pretrained(device="cpu")
        self.default_conds = self.model.conds

    def _conditionals(self, voice: str):
        if voice == DEFAULT_VOICE:
            return self.default_conds
        path = os.path.join(self.directory, voice + CLIP_SUFFIX)
        mtime = os.path.getmtime(path)
        have = self.conds.get(voice)
        if have and have[0] == mtime:
            return have[1]
        # prepare_conditionals sets model.conds from the clip; kept per
        # voice, so a clone is analysed once and "default" stays default.
        self.model.prepare_conditionals(path, exaggeration=self._exaggeration())
        self.conds[voice] = (mtime, self.model.conds)
        return self.model.conds

    @staticmethod
    def _exaggeration() -> float:
        return _server.env_float("CHATTERBOX_EXAGGERATION", 0.5)

    def synth(self, text: str, voice: str):
        self.model.conds = self._conditionals(voice)
        wav = self.model.generate(text, exaggeration=self._exaggeration(),
                                  cfg_weight=_server.env_float("CHATTERBOX_CFG_WEIGHT", 0.5))
        return wav.squeeze(0).detach().cpu().numpy(), int(self.model.sr)

    def close(self) -> None:
        self.model = None


def main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(prog="chatterbox-tts-server",
                                 description="Local Chatterbox TTS over HTTP (WAV).")
    ap.add_argument("--host", default=os.environ.get("CHATTERBOX_BIND") or "127.0.0.1")
    ap.add_argument("--port", type=int, default=_server.env_int("CHATTERBOX_PORT", DEFAULT_PORT))
    ap.add_argument("--voices-dir", default=voices_dir())
    ap.add_argument("--idle", type=float, default=_server.env_float("CHATTERBOX_IDLE_S", 600))
    a = ap.parse_args(argv)
    import importlib.util
    if importlib.util.find_spec("chatterbox") is None:   # found, not imported: torch loads at the first /tts
        print("chatterbox-tts-server: chatterbox is not installed — install the [chatterbox] extra "
              "(media-setup voices chatterbox)", file=sys.stderr)
        return 2
    default = os.environ.get("CHATTERBOX_VOICE") or DEFAULT_VOICE
    service = _server.Service(ChatterboxBackend(a.voices_dir), default_voice=default)
    return _server.serve(service, a.host, a.port, a.idle,
                         f"chatterbox-tts (voices: {', '.join(service.voices())})")


if __name__ == "__main__":
    raise SystemExit(main())
