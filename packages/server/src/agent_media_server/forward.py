"""`POST /session/forward` — a message sent on to other chats
(server-contract.md §6.40).

From the list of what the bridged apps do (David, 20 Oct 2026, per-chat
controls: "forward one to another chat"; the second round's "send to
several chats at once"). A hold on a message offers Forward…, a picker
chooses one chat or several, and each gets the message:

* **to a room**: its words as a message of yours; a photo, video, voice
  note or file from a room goes as itself, the same file on the homeserver
  (no second upload), so the bridge sends the network's own photo.
* **to an agent's chat**: the words, opening with where they came from
  (`Forwarded from <chat>:`); a room's photo or file is fetched and goes as
  a `Shared file:` line the agent can open.

From a room the server takes the message as it holds it; from an agent's
chat the app sends its words (`text`). Each chat is tried; the answer says
which took it and which did not.
"""

from __future__ import annotations

import logging
import shutil
import time

from . import auth, sessions

log = logging.getLogger("agent-media.server.forward")

#: Chats one forward reaches, at most.
MAX_TO = 20

_MSGTYPE = {"image": "m.image", "video": "m.video", "audio": "m.audio", "voice": "m.audio",
            "file": "m.file"}


def _from_room(room, mid: str) -> dict | None:
    """`{"text", "media"?: (content, info)}` for message `mid` of `room`."""
    with room.lock:
        m = room._by_id.get(mid)
        if m is None:
            return None
        parts = [dict(p) for p in m["parts"]]
    words = "\n".join(p.get("text") or "" for p in parts if p.get("type") == "text").strip()
    media = next((p for p in parts if p.get("type") == "media"), None)
    out: dict = {"text": words}
    info = room.media.get(mid) if media else None
    if media and info:
        content = {"msgtype": _MSGTYPE.get(media.get("kind") or "", "m.file"),
                   "body": media.get("name") or media.get("text") or "file",
                   "url": info["mxc"],
                   "info": {k: media[k] for k in ("mimetype", "size", "w", "h", "duration")
                            if media.get(k) not in (None, "")}}
        if media.get("name"):
            content["filename"] = media["name"]
        if media.get("kind") == "voice":
            content["org.matrix.msc3245.voice"] = {}
        out["media"] = (content, info)
    return out


def _shared_copy(info: dict, name: str) -> str | None:
    """A room's file put under the upload root, for an agent to open."""
    from . import room_media, uploads

    path = room_media.fetch(info, thumb=False)
    if path is None:
        return None
    day = uploads.root() / time.strftime("%Y-%m-%d")
    day.mkdir(parents=True, exist_ok=True)
    dest = day / uploads.safe_name(name or path.name)
    if dest.exists():
        dest = day / f"{dest.stem}-{int(time.time())}{dest.suffix}"
    shutil.copyfile(path, dest)
    return str(dest)


def session_forward(session: str, mid: str, to: list, bearer: str, *,
                    text: str = "") -> tuple[bool, dict]:
    """`{"session", "id", "to": [session…], "text"?}` → `{"session", "id",
    "sent": [session…], "failed": [{"session", "error"}]}`."""
    session = (session or "").strip()
    if not sessions._SESSION.fullmatch(session):
        return False, {"error": "not a session id", "status": 400}
    if not isinstance(to, list) or not to or len(to) > MAX_TO:
        return False, {"error": f"forward needs 1 to {MAX_TO} chats in `to`", "status": 400}
    targets = [str(t or "").strip() for t in to]
    if any(not sessions._SESSION.fullmatch(t) for t in targets):
        return False, {"error": "not a session id in `to`", "status": 400}
    user, err = auth.gate(bearer)
    if not user:
        return False, err
    from . import matrix, send

    src_room = matrix.room(session)
    if src_room is not None:
        got = _from_room(src_room, (mid or "").strip())
        if got is None:
            return False, {"error": "no such message in this room", "status": 404}
    else:
        got = {"text": (text or "").strip()}
    if not got.get("text") and not got.get("media"):
        return False, {"error": "nothing to forward", "status": 400}
    origin = sessions.title_of(session) or "another chat"
    sent, failed, shared = [], [], None
    for t in dict.fromkeys(targets):
        if t == session:
            continue
        room = matrix.room(t)
        try:
            if room is not None:
                ok, detail = True, {}
                if got.get("media"):
                    ok, detail = room.send_content(got["media"][0])
                if ok and got.get("text"):
                    ok, detail = room.send(got["text"])
            else:
                lines = [f"Forwarded from {origin}:"]
                if got.get("text"):
                    lines.append(got["text"])
                if got.get("media"):
                    if shared is None:
                        content, info = got["media"]
                        shared = _shared_copy(info, content.get("filename") or content["body"]) or ""
                    if shared:
                        lines.append(f"Shared file: {shared}")
                ok, detail = send.reply("", "\n".join(lines), bearer, session=t)
        except Exception as e:  # noqa: BLE001 — one chat, not the rest
            log.warning("forward to %s: %s", t[:8], e)
            ok, detail = False, {"error": str(e)}
        if ok:
            sent.append(t)
        else:
            failed.append({"session": t, "error": str(detail.get("error") or "not sent")})
    return True, {"session": session, "id": mid, "sent": sent, "failed": failed}
