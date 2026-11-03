"""A voice clip sent to an agent, with its words (server-contract.md §6.36).

David, 29 Oct 2026: a voice clip recorded in the app reaches an agent as a
`Shared file:` line it can open but not hear — "Maybe we need a speech to
text solution too". Now each clip's line (an audio file named
`voice-clip-…`, room_send.VOICE_PREFIX) is followed by what was said:

    Shared file: /home/…/voice-clip-20261029-1801.webm
    (Voice clip, transcribed: "can you pick up some milk on the way home")

by whichever speech-to-text is set up (agent_media_core.transcribe). With
none set up, or when it fails, the line goes as before: the clip is still
there for the agent to open, and a send never waits on more than one try.
"""

from __future__ import annotations

import logging
import mimetypes

log = logging.getLogger("agent-media.server.voice_clips")


def with_transcripts(text: str) -> str:
    """`text` with each voice clip's words after its line."""
    from agent_media_core import transcribe

    from . import room_send, uploads

    if "Shared file: " not in (text or "") or room_send.VOICE_PREFIX not in text:
        return text
    if transcribe.provider() is None:
        return text
    out = []
    for ln in text.split("\n"):
        out.append(ln)
        m = room_send._LINE.match(ln)
        if not m:
            continue
        ok, found = uploads.find(m.group(1), "", signed=True)
        if not ok:
            continue
        name = found["file"].name
        ctype = mimetypes.guess_type(name)[0] or found["type"]
        if not room_send.is_voice(name, ctype):
            continue
        try:
            words = transcribe.transcribe(found["file"])
        except transcribe.SttError as e:
            log.info("voice clip %s not transcribed: %s", name, e)
            continue
        words = words.replace('"', "'")
        out.append(f'(Voice clip, transcribed: "{words}")' if words
                   else "(Voice clip: nothing was said.)")
    return "\n".join(out)


def session_transcribe(path: str, bearer: str) -> tuple[bool, dict]:
    """`POST /transcribe {"path"}` → `{"text", "provider"}`: the words of a
    clip you have attached (a shared file, §6.18), to read before sending it
    (David, 30 Oct 2026: "a way of viewing the transcript of the voice note
    before we send it"). Cached by the clip, so sending it to an agent after
    asks nothing again. 404 for anything not a shared file, 409 with no
    speech-to-text set up, 502 when the service fails."""
    from agent_media_core import transcribe

    from . import uploads

    ok, found = uploads.find(path or "", bearer)
    if not ok:
        return False, found
    p = transcribe.provider()
    if p is None:
        return False, {"error": "no speech-to-text is set up", "status": 409}
    try:
        words = transcribe.transcribe(found["file"])
    except transcribe.SttError as e:
        return False, {"error": f"not transcribed: {e}", "status": 502}
    return True, {"text": words, "provider": p["name"]}


#: The most a stretch of dictation may be: a few seconds to ~30 s of opus
#: is well under 1 MiB, so this leaves room without inviting a recording.
AUDIO_MAX = 8 * 1024 * 1024

#: A recording's Content-Type → the extension it goes to the service with
#: (the clip path's extension does this in the `{path}` form).
_EXT = {"audio/webm": "webm", "audio/ogg": "ogg", "audio/opus": "ogg",
        "audio/mp4": "m4a", "audio/m4a": "m4a", "audio/x-m4a": "m4a", "audio/aac": "aac",
        "audio/mpeg": "mp3", "audio/mp3": "mp3", "audio/wav": "wav", "audio/x-wav": "wav",
        "audio/wave": "wav", "audio/flac": "flac", "audio/x-flac": "flac"}


def is_audio_body(ctype: str) -> bool:
    """Whether a `POST /transcribe` carries the recording itself."""
    c = (ctype or "").split(";", 1)[0].strip().lower()
    return c.startswith("audio/") or c == "application/octet-stream"


def _ext(ctype: str, head: bytes) -> str:
    c = (ctype or "").split(";", 1)[0].strip().lower()
    if c in _EXT:
        return _EXT[c]
    # application/octet-stream or an audio type not listed: by its first bytes.
    if head.startswith(b"OggS"):
        return "ogg"
    if head.startswith(b"\x1a\x45\xdf\xa3"):
        return "webm"
    if head.startswith(b"RIFF"):
        return "wav"
    if head.startswith(b"fLaC"):
        return "flac"
    if head[4:8] == b"ftyp":
        return "m4a"
    if head.startswith(b"ID3") or head[:1] == b"\xff":
        return "mp3"
    return "webm"                 # a WebView's MediaRecorder, by default


def session_transcribe_audio(rfile, length: int, ctype: str, bearer: str,
                             language: str = "") -> tuple[bool, dict]:
    """`POST /transcribe` with the recording as the body (Content-Type
    `audio/*` or `application/octet-stream`) → `{"text", "ms", "provider"}`:
    the words of one stretch of dictation, split at the speaker's pauses by
    the app (David, 3 Nov 2026: a dictation mode where the app records and
    the server transcribes each stretch). Nothing is kept but the cached
    transcript. 401/403 for a stranger (checked before the body is read),
    400 for an empty body, 413 over AUDIO_MAX, 503 with no speech-to-text
    set up, 502 when the service fails."""
    import tempfile
    import time
    from pathlib import Path

    from agent_media_core import transcribe

    from . import auth

    user, err = auth.gate(bearer)
    if not user:
        return False, err
    if length <= 0:
        return False, {"error": "no audio was sent", "status": 400}
    if length > AUDIO_MAX:
        return False, {"error": "the recording is too long to transcribe", "status": 413}
    p = transcribe.provider()
    if p is None:
        return False, {"error": "no speech-to-text is set up on this server", "status": 503}
    data = rfile.read(length)
    if len(data) < length:
        return False, {"error": "the recording did not all arrive", "status": 400}
    t0 = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="transcribe-") as tmp:
        clip = Path(tmp) / f"stretch.{_ext(ctype, data[:16])}"
        clip.write_bytes(data)
        try:
            words = transcribe.transcribe(clip, language=language)
        except transcribe.SttError as e:
            return False, {"error": f"not transcribed: {e}", "status": 502}
    return True, {"text": words, "ms": int((time.monotonic() - t0) * 1000),
                  "provider": p["name"]}
