"""Photos, files and voice clips sent to a room (server-contract.md §6.33).

David, 20 Oct 2026, from the list of what the bridged apps do: send a photo
or a file; 22 Oct: "attach a voice clip to a message, including a reply to
a particular message". 29 Oct: "Send photos and voice clips".

The app attaches a file the way it always has: `POST /upload` keeps it under
the upload root and the message carries a `Shared file: <path>` line for
it. Sent to an agent, that line is how the agent opens it. Sent to a Matrix
room it would be a path on this host typed to a person, so here each line
becomes the file itself: uploaded to the homeserver as the owner and sent
as an `m.image`, `m.video`, `m.audio` or `m.file`, which the bridge sends
as the network's own photo, video, voice note or file. The words, if any,
follow as a message of their own. A reply (§6.31) is the first of them.

A **voice clip** is an audio file whose name starts `voice-clip-` (the
app's recorder names it so): it is sent as a voice note
(`org.matrix.msc3245.voice`, with its length), and made Ogg Opus first if
it is not, since that is what WhatsApp and Signal play as one. Only files
under the upload root are sent (`uploads.find`); any other path is refused.
"""

from __future__ import annotations

import json
import logging
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

log = logging.getLogger("agent-media.server.room_send")

_LINE = re.compile(r"^Shared file: (\S.*?)\s*$")
VOICE_PREFIX = "voice-clip-"


def split(text: str) -> tuple[list[str], str]:
    """(the shared files' paths, the words without their lines)."""
    paths, words = [], []
    for ln in (text or "").split("\n"):
        m = _LINE.match(ln)
        if m:
            paths.append(m.group(1))
        else:
            words.append(ln)
    return paths, "\n".join(words).strip()


def _kind(ctype: str) -> str:
    major = ctype.split("/", 1)[0]
    return {"image": "m.image", "video": "m.video", "audio": "m.audio"}.get(major, "m.file")


def _duration_ms(path: Path) -> int | None:
    if not shutil.which("ffprobe"):
        return None
    try:
        out = subprocess.run(["ffprobe", "-v", "quiet", "-print_format", "json",
                              "-show_format", str(path)],
                             capture_output=True, timeout=20, check=False).stdout
        return int(float(json.loads(out or b"{}")["format"]["duration"]) * 1000)
    except (OSError, ValueError, KeyError, subprocess.SubprocessError):
        return None


def _as_ogg(path: Path, tmp: Path) -> Path | None:
    """The clip as Ogg Opus: the Opus in a WebM copied across, else
    re-encoded. None when ffmpeg is missing or fails."""
    if not shutil.which("ffmpeg"):
        return None
    out = tmp / (path.stem + ".ogg")
    for codec in (["-c:a", "copy"], ["-c:a", "libopus", "-b:a", "32k"]):
        r = subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(path), "-vn", *codec,
                            str(out)], capture_output=True, timeout=60, check=False)
        if r.returncode == 0 and out.is_file() and out.stat().st_size:
            return out
    return None


def is_voice(name: str, ctype: str) -> bool:
    return name.startswith(VOICE_PREFIX) and ctype.startswith(("audio/", "video/webm"))


def content_for(room, path: Path, ctype: str, size: int) -> tuple[bool, dict]:
    """The `m.room.message` content for one shared file, uploaded."""
    name = path.name
    voice = is_voice(name, ctype)
    with tempfile.TemporaryDirectory() as tmp:
        send_path = path
        if voice and not ctype.startswith("audio/ogg"):
            ogg = _as_ogg(path, Path(tmp))
            if ogg is not None:
                send_path, ctype, name = ogg, "audio/ogg", ogg.name
            else:
                ctype = "audio/webm" if ctype.startswith("video/webm") else ctype
        data = send_path.read_bytes()
        ok, got = room.upload(data, name, ctype.split(";")[0])
        if not ok:
            return False, got
        info: dict = {"mimetype": ctype.split(";")[0], "size": len(data) if voice else size}
        duration = _duration_ms(send_path) if (voice or ctype.startswith(("audio/", "video/"))) \
            else None
    content: dict = {"msgtype": "m.audio" if voice else _kind(ctype), "body": name,
                     "filename": name, "url": got["url"], "info": info}
    if duration:
        info["duration"] = duration
    if voice:
        content["org.matrix.msc3245.voice"] = {}
        content["org.matrix.msc1767.audio"] = {"duration": duration or 0}
    return True, content


def send(room, text: str, reply_to: str = "") -> tuple[bool, dict]:
    """`text` to `room`: each `Shared file:` line as the file, then the
    words. Stops at the first that fails, and says how far it got."""
    from . import uploads

    paths, words = split(text)
    if not paths:
        return room.send(text, reply_to)
    files = []
    for p in paths:
        ok, found = uploads.find(p, "", signed=True)
        if not ok:
            return False, {"error": f"not a shared file: {Path(p).name}", "status": 400}
        files.append(found)
    detail: dict = {}
    sent = 0
    for f in files:
        ok, content = content_for(room, f["file"], f["type"], f["size"])
        if ok:
            ok, detail = room.send_content(content, reply_to if not sent else "")
        else:
            detail = content
        if not ok:
            if sent:
                detail["error"] = f"{detail.get('error')} (after {sent} of {len(files)} sent)"
            return False, detail
        sent += 1
    if words:
        return room.send(words)
    return True, detail
