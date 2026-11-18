"""The slow, expressive local voices for agent-media: Chatterbox and Orpheus.

Registered under `agent_media.render_engines` as `chatterbox` and `orpheus`.
Each POSTs text to its own server on this machine (`chatterbox-tts-server`
on 127.0.0.1:8882, `orpheus-tts-server` on 127.0.0.1:8883, installed as
user units by `media-setup voices chatterbox|orpheus`) and writes back the
WAV. Nothing leaves the machine. The servers hold their model only while
used and exit when idle; the client starts the unit when it finds none
running (client.py). Both are several times slower than speech, so a reply
in them is rendered whole before it plays
(docs/proposals/2026-11-14-voice-picker-providers.md, step 6).

Config (env):

  MEDIA_CHATTERBOX_BASE_URL      default http://127.0.0.1:8882
  MEDIA_RENDER_VOICE_CHATTERBOX  default "default" (or a clone's name)
  MEDIA_CHATTERBOX_TIMEOUT_S     one render (default 600)
  MEDIA_CHATTERBOX_START_WAIT_S  wait for a started server (default 60)
  MEDIA_ORPHEUS_BASE_URL         default http://127.0.0.1:8883
  MEDIA_RENDER_VOICE_ORPHEUS     default tara
  MEDIA_ORPHEUS_TIMEOUT_S        one render (default 600)
  MEDIA_ORPHEUS_START_WAIT_S     wait for a started server (default 60)

The client is stdlib only; the servers need the [chatterbox] / [orpheus]
extras, each in its own venv.
"""
from __future__ import annotations

from pathlib import Path

from .client import CHATTERBOX, ORPHEUS, render_with


def render_chatterbox(text: str, outfile: Path, *, voice: str | None = None) -> tuple[bool, str]:
    """Render `text` to a WAV at `outfile` with Chatterbox."""
    return render_with(CHATTERBOX, text, Path(outfile), voice=voice)


def render_orpheus(text: str, outfile: Path, *, voice: str | None = None) -> tuple[bool, str]:
    """Render `text` to a WAV at `outfile` with Orpheus."""
    return render_with(ORPHEUS, text, Path(outfile), voice=voice)


__all__ = ["render_chatterbox", "render_orpheus"]
