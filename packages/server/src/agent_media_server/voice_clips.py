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
