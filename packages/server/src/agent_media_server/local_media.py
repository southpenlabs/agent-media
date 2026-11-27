"""Full copy's media bytes: a Local chat message's photo, video, sound or file,
sent once when its account moves from the phone to the server
(server-contract.md §6.61 "Media bytes"; sasonica-app mail #1417, David,
28 Nov 2026: copy the files when an account moves, once, not day to day).

Full copy keeps a message's media as its name, type and size ("photo — on
the phone"). When a home changes from phone to host the app sends each
message's bytes here (`POST /local/media?chat=&event=`), and from then on
the message's media part carries a `url` in its log (and in the stitched
log of its twin room, twins.py), served like any attachment.

Kept as files, not in the database: `<state_dir>/local-media/<chat hash>/
<event hash>` (folders 0700, files 0600, written to a `.part` and renamed),
with the type beside it in `<event hash>.type`. They go with their messages
(local_messages._delete: the dial leaving full, a chat pruned).
"""

from __future__ import annotations

import hashlib
import os
import shutil
from pathlib import Path
from urllib.parse import quote

from . import local_chats, local_messages
from agent_media_core import failures as _failures

#: The default largest file, in MB (`MEDIA_LOCAL_MEDIA_MAX_MB`).
MAX_MB = 100
CHUNK = 1 << 20
_TYPE = __import__("re").compile(r"[a-z0-9][a-z0-9.+-]*/[a-z0-9][a-z0-9.+-]*")


def max_bytes(env=None) -> int:
    env = os.environ if env is None else env
    try:
        mb = int((env.get("MEDIA_LOCAL_MEDIA_MAX_MB") or "").strip() or MAX_MB)
    except ValueError:
        mb = MAX_MB
    return max(1, mb) << 20


def root() -> Path:
    return local_messages.db_path().parent / "local-media"


def _h(s: str, n: int) -> str:
    return hashlib.sha256(s.encode()).hexdigest()[:n]


def chat_dir(chat: str) -> Path:
    return root() / _h(chat, 24)


def path_of(chat: str, event: str) -> Path:
    return chat_dir(chat) / _h(event, 40)


def held(chat: str, event: str) -> bool:
    return path_of(chat, event).is_file()


def url(chat: str, event: str) -> str:
    return f"/local/media?chat={quote(chat, safe='')}&event={quote(event, safe='')}"


def _media_row(chat: str, event: str):
    if not local_messages._exists():
        return None
    return local_messages._connect().execute(
        "SELECT kind, media, redacted FROM msgs WHERE chat=? AND id=?", (chat, event)).fetchone()


def save(rfile, length: int, query: dict, headers, bearer: str) -> tuple[bool, dict, bool]:
    """`POST /local/media?chat=&event=`, the body the bytes: `(ok, detail,
    whether the body was read)`. Refused unread when the caller may not send
    them or the length is past the limit."""
    chat = (query.get("chat") or [""])[0]
    event = (query.get("event") or [""])[0]
    ok, err = local_messages._bridging(chat, event, bearer)
    if not ok:
        if err.get("code") == "not_bridging":
            err["error"] = "only the device that bridges this chat sends its media"
        if "sender" in str(err.get("error")):
            err["error"] = "event is the event id of a message of the chat"
        return False, err, False
    limit = max_bytes()
    if length <= 0:
        return False, {"error": "the body is the file's bytes, with a Content-Length",
                       "status": 411, "code": "length_required"}, False
    if length > limit:
        return False, {"error": f"a file is at most {limit >> 20} MB", "status": 413,
                       "code": "too_big", "limit": limit}, False
    row = _media_row(chat, event)
    if row is None:
        return False, {"error": "no such message in this chat", "status": 404,
                       "code": "not_found"}, False
    kind, media, redacted = row
    if kind not in local_messages.MEDIA_KINDS:
        return False, {"error": "that message has no media", "status": 409,
                       "code": "no_media"}, False
    if redacted:
        return False, {"error": "that message was deleted", "status": 409,
                       "code": "deleted"}, False
    ctype = (headers.get("Content-Type") or "").split(";")[0].strip().lower()
    if not _TYPE.fullmatch(ctype) or ctype == "application/octet-stream":
        import json

        ctype = str((json.loads(media) if media else {}).get("mime") or "") or ctype
    target = path_of(chat, event)
    target.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(root(), 0o700)
    os.chmod(target.parent, 0o700)
    part = target.with_name(target.name + ".part")
    got = 0
    try:
        fd = os.open(part, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "wb") as f:
            while got < length:
                chunk = rfile.read(min(CHUNK, length - got))
                if not chunk:
                    break
                f.write(chunk)
                got += len(chunk)
        if got != length:
            part.unlink(missing_ok=True)
            return False, {"error": "the body ended before its Content-Length", "status": 400,
                           "code": "short_body"}, True
        os.replace(part, target)
        tfd = os.open(target.with_name(target.name + ".type"),
                      os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(tfd, "w") as f:
            f.write(ctype if _TYPE.fullmatch(ctype or "") else "")
    except OSError as e:
        part.unlink(missing_ok=True)
        return False, {"error": f"could not keep it: {e}", "status": 507}, True
    return True, {"chat": chat, "event": event, "size": got, "type": ctype or None,
                  "url": url(chat, event)}, True


def open_media(chat: str, event: str, bearer: str) -> tuple[bool, dict]:
    """`GET /local/media?chat=&event=`: `{file, size, type}` to whoever may
    read the chat, or the refusal (404 when its bytes are not here)."""
    ok, err = local_messages.may_read(bearer)
    if not ok:
        return ok, err
    p = path_of(chat, event) if local_chats.is_local(chat) and event else None
    if p is None or not p.is_file() or _media_row(chat, event) is None:
        return False, {"error": "this file is on the phone, not here", "status": 404,
                       "code": "not_found"}
    try:
        ctype = p.with_name(p.name + ".type").read_text().strip()
    except OSError:
        ctype = ""
    return True, {"file": str(p), "size": p.stat().st_size,
                  "type": ctype if ctype.split("/")[0] in ("image", "video", "audio")
                  else "application/octet-stream"}


def missing(chat: str, bearer: str) -> tuple[bool, dict]:
    """`GET /local/media/missing?chat=`: the chat's media messages whose bytes
    are not here yet, newest first, so a move cut short resumes."""
    ok, err = local_messages.may_read(bearer)
    if not ok:
        return ok, err
    if not isinstance(chat, str) or not local_chats.is_local(chat):
        return False, {"error": "chat is a Local chat's id, local:<network>:<id>", "status": 400}
    if local_chats.record(chat) is None:
        return False, {"error": "no such chat: a phone has not listed it", "status": 404,
                       "code": "not_found"}
    out = []
    if local_messages._exists():
        kinds = local_messages.MEDIA_KINDS
        for mid, kind, media in local_messages._connect().execute(
                f"SELECT id, kind, media FROM msgs WHERE chat=? AND redacted=0 AND kind IN "
                f"({','.join('?' * len(kinds))}) ORDER BY at DESC, seq DESC", (chat, *kinds)):
            if not held(chat, mid):
                import json

                size = (json.loads(media) if media else {}).get("size")
                out.append({"event": mid, "kind": kind, "size": size})
    return True, {"chat": chat, "missing": out, "limit": max_bytes()}


def forget(chats: list[str] | None) -> None:
    """The files of `chats` (every chat with None) gone with their messages."""
    try:
        if chats is None:
            shutil.rmtree(root(), ignore_errors=True)
        else:
            for c in chats:
                shutil.rmtree(chat_dir(c), ignore_errors=True)
    except Exception as _exc:  # noqa: BLE001 — a file left behind is only space
        _failures.swallowed(_exc)
