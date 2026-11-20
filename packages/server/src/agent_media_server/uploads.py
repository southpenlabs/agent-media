"""A file shared to the app, kept on the host (`POST /upload`).

The phone's share sheet hands the app a photo, a PDF, a recording; the app
sends each one here as the raw request body, named by `?name=`, and puts the
path it gets back into the words it is about to send, so the assistant can
open the file. Files land in a folder per day under ~/shared (MEDIA_UPLOAD_DIR
moves it), never over another: a second `photo.jpg` that day is `photo-2.jpg`.

The body is streamed to disk, never held in memory, and only after the
bearer has been checked: the 64 KiB cap on every other route (#139) is lifted
for this one path, to MEDIA_UPLOAD_MAX_MB (512 by default).

`GET /upload?path=` hands one back, so the app can draw a shared photo as a
thumbnail in the reply box and in the thread. Only a file under the upload
root is served — the path is resolved, symlinks and all, before it is checked
— and anything else is a 404, the same answer as a file that is not there.
"""

from __future__ import annotations

import datetime as dt
import mimetypes
import os
import re
from pathlib import Path
from typing import BinaryIO

from . import auth
from agent_media_core import failures as _failures

CHUNK = 1 << 20


def max_bytes() -> int:
    try:
        mb = int(os.environ.get("MEDIA_UPLOAD_MAX_MB") or 512)
    except ValueError:
        mb = 512
    return max(1, mb) << 20


def root() -> Path:
    from agent_media_core import shared_files

    return shared_files.root()


_UNSAFE = re.compile(r"[\x00-\x1f\x7f/\\:*?\"<>|]+")


def safe_name(name: str) -> str:
    """The last part of `name`, with nothing a shell or a path would trip on,
    no leading dot (a shared `.bashrc` is not a hidden file here), and short."""
    name = (name or "").replace("\\", "/").rsplit("/", 1)[-1]
    name = _UNSAFE.sub("-", name).strip(" .-")
    if not name:
        return "shared"
    if len(name) > 120:
        stem, dot, ext = name.rpartition(".")
        name = (stem[:110] + "." + ext[:9]) if dot and 0 < len(ext) <= 9 else name[:120]
    return name


def free(folder: Path, name: str) -> Path:
    p = folder / name
    if not p.exists():
        return p
    stem, dot, ext = name.rpartition(".")
    if not dot or not stem:
        stem, ext = name, ""
    n = 2
    while True:
        p = folder / (f"{stem}-{n}.{ext}" if ext else f"{stem}-{n}")
        if not p.exists():
            return p
        n += 1


def _sha256_of(p: Path) -> str:
    import hashlib

    digest = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _kept_copy(bearer: str, sha256: str, size: int, folder: Path) -> Path | None:
    """The file this device already kept today with these bytes, or None.

    The app retries a share whose send failed (sasonica-app 5bab4980), and
    when the server had kept the file before the connection broke, the retry
    would be a second copy. Only this device's own kept uploads in `folder`
    (the upload log's saved lines) count, and only while the file is still
    there with the same bytes: one deleted or changed since is not reused."""
    from . import upload_log

    me = upload_log.who(bearer).get("id")
    if not me:
        return None
    try:
        lines = upload_log.read(days=2)
    except Exception as _exc:  # noqa: BLE001 — no log, no reuse: keep the upload
        _failures.swallowed(_exc)
        return None
    for line in reversed(lines):
        if (line.get("outcome") != "saved" or line.get("sha256") != sha256
                or line.get("size") != size
                or (line.get("device") or {}).get("id") != me):
            continue
        p = Path(str(line.get("path") or ""))
        try:
            if (p.parent.resolve() != folder.resolve() or not p.is_file()
                    or p.stat().st_size != size or _sha256_of(p) != sha256):
                continue
        except OSError:
            continue
        return p
    return None


def save(body: BinaryIO, length: int, name: str, bearer: str,
         today: dt.date | None = None) -> tuple[bool, dict]:
    """Write `length` bytes of `body` as `name` in today's folder. Each call,
    kept or refused, leaves a line in the upload log (upload_log.py)."""
    from . import upload_log

    def refused(status: int, why: str, **more) -> tuple[bool, dict]:
        upload_log.record(more.pop("outcome", "refused"), bearer=bearer, status=status, why=why,
                          name=name, size=length, **more)
        return False, {"error": why, "status": status}

    user, err = auth.gate(bearer)
    if not user:
        upload_log.record("refused", bearer=bearer, status=err.get("status", 401),
                          why=str(err.get("error") or err.get("code") or "not a device"),
                          name=name, size=length)
        return False, err
    if length <= 0:
        return refused(411, "nothing sent (Content-Length is required)")
    if length > max_bytes():
        return refused(413, f"too large (the limit is {max_bytes() >> 20} MB)")
    import hashlib

    digest = hashlib.sha256()
    left = length
    reading = False      # where an OSError came from: the body (cut off) or the disk
    part = None
    try:
        folder = root() / (today or dt.date.today()).isoformat()
        folder.mkdir(parents=True, exist_ok=True)
        final = free(folder, safe_name(name))
        part = folder / f".{final.name}.part"
        with open(part, "wb") as out:
            while left > 0:
                reading = True
                chunk = body.read(min(CHUNK, left))
                if not chunk:
                    break
                reading = False
                digest.update(chunk)
                out.write(chunk)
                left -= len(chunk)
        reading = False
        if left:
            reading = True
            raise OSError(f"the upload stopped {left} bytes short")
        same = _kept_copy(bearer, digest.hexdigest(), length, folder)
        if same is not None:
            part.unlink(missing_ok=True)
            upload_log.record("duplicate", bearer=bearer, status=200, name=name, size=length,
                              received=length, sha256=digest.hexdigest(), where=str(same))
            return True, {"path": str(same), "name": same.name, "size": length,
                          "duplicate": True}
        final = free(folder, final.name)
        os.replace(part, final)
    except OSError as e:
        if part is not None:
            part.unlink(missing_ok=True)
        return refused(400, str(e), outcome="cut_off" if reading else "failed",
                       received=length - left, sha256=digest.hexdigest())
    upload_log.record("saved", bearer=bearer, status=200, name=name, size=length,
                      received=length, sha256=digest.hexdigest(), where=str(final))
    return True, {"path": str(final), "name": final.name, "size": length}


#: How long a signed link to a shared file lasts (`link`): a video watched in one sitting.
LINK_S = 6 * 3600


def _link_sig(path: str, exp: int) -> str:
    """The signature of a link to `path` until `exp`: an HMAC under the host's
    signing key (agent_media_core.clip_sign), never a device's own token."""
    import hashlib
    import hmac

    from agent_media_core import clip_sign

    return hmac.new(clip_sign._key(), f"upload:{path}:{exp}".encode(), hashlib.sha256).hexdigest()[:40]


def link(path: str, bearer: str, now: float | None = None) -> tuple[bool, dict]:
    """`POST /upload/link {path}` → a link to a shared file that opens it with
    no bearer, for a `<video>` (it cannot send one) to stream and seek, good
    for LINK_S. Only a file `GET /upload` would give the caller."""
    ok, detail = find(path, bearer)
    if not ok:
        return False, detail
    import time
    from urllib.parse import urlencode

    real = str(detail["file"])
    exp = int((now if now is not None else time.time()) + LINK_S)
    return True, {"url": "/upload?" + urlencode({"path": real, "exp": exp, "sig": _link_sig(real, exp)}),
                  "expires": exp, "type": detail["type"]}


def signed_ok(path: str, exp: str, sig: str, now: float | None = None) -> bool:
    """A link `link` made for this path, not yet expired."""
    import hmac
    import time

    try:
        when = int(exp)
        real = str(Path(os.path.expanduser(path)).resolve())
    except (ValueError, OSError, RuntimeError):
        return False
    if when < (now if now is not None else time.time()) or not sig:
        return False
    return hmac.compare_digest(_link_sig(real, when), sig)


def find(path: str, bearer: str, signed: bool = False) -> tuple[bool, dict]:
    """The shared file at `path`, if it is one: its resolved path, size and
    Content-Type. Anything outside the upload root, a folder, a `.part`
    still being written, or a file that is not there is the same 404.
    `signed`: a link `link` made was checked instead of a bearer."""
    if not signed:
        user, err = auth.gate(bearer)
        if not user:
            return False, err
    missing = {"error": "no such shared file", "status": 404}
    if not path or "\x00" in path:
        return False, missing
    try:
        top = root().resolve()
        p = Path(os.path.expanduser(path)).resolve()
    except (OSError, RuntimeError, ValueError):
        return False, missing
    if not p.is_relative_to(top) or p == top or not p.is_file() or p.name.startswith("."):
        return False, missing
    ctype = mimetypes.guess_type(p.name)[0] or "application/octet-stream"
    if ctype.startswith("text/"):
        ctype += "; charset=utf-8"
    return True, {"file": p, "size": p.stat().st_size, "type": ctype}
