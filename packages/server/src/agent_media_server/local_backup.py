"""Backups of the phone's Local chats, kept as ciphertext (server-contract.md
§6.61, "Backup").

Step 4 of docs/proposals/2026-11-17-phone-bridges-server-integration.md
(David, 17 Nov 2026: "Do the backup"). The phone's companion, Sasonica
Bridges, packs its local chat store and media (never the networks' sign-ins:
each network is paired again after a restore) into one bundle, encrypted
with a key only the user holds (a recovery code). Sasonica uploads it here
with its own device token. The server keeps the bytes and cannot read them:
nothing here opens, parses or logs a bundle's contents.

**Where**: `<state_dir>/local-backups/<device id>/<created>-<sha8>.sbb`
(folders 0700, files 0600), with a `.json` beside each bundle saying what
the upload said of it (created, size, sha256, format, size_plain) and whose
it was (the device, its name then, and its account), so a phone that was
reinstalled and paired again, a new device id, still finds the bundles its
old pairing made when both belong to one signed-in account.

**Upload** (`POST /local/backup`): the raw body, streamed to a `.part` file
a chunk at a time while its SHA-256 is taken, never held in memory; a body
over `MEDIA_LOCAL_BACKUP_MAX_MB` (2048) is refused unread, one whose hash is
not the `sha256` sent is deleted and refused. Only then does it take its
name, and the device's oldest bundles past `MEDIA_LOCAL_BACKUP_KEEP` (7) are
deleted.

**Who sees one**: an admin device every bundle; any other paired device the
bundles of its own device, and of every device enrolled by the same account
(`iss`/`sub`), so a person's own phones share theirs and no one else's. A
guest none (403 `guest`). A bundle the caller may not see is answered as one
that is not there (404).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
from pathlib import Path
from typing import BinaryIO

from . import auth

CHUNK = 1 << 20
DIR = "local-backups"
EXT = ".sbb"
#: A bundle's id: `<device id>.<created>-<first 8 of its sha256>`.
ID = re.compile(r"([A-Za-z0-9_-]{1,64})\.(\d{1,12})-([0-9a-f]{8})")
DEVICE = re.compile(r"[A-Za-z0-9_-]{1,64}")
FORMAT = re.compile(r"[A-Za-z0-9._-]{1,32}")
SHA = re.compile(r"[0-9a-f]{64}")

_LOCK = threading.Lock()

GUEST = {"code": "guest", "status": 403, "error": "a guest has no backups here"}
NOT_FOUND = {"code": "not_found", "status": 404, "error": "no such backup"}


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name) or default)
    except ValueError:
        return default


def max_bytes() -> int:
    """The largest bundle taken (`MEDIA_LOCAL_BACKUP_MAX_MB`, 2048)."""
    return max(1, _int_env("MEDIA_LOCAL_BACKUP_MAX_MB", 2048)) << 20


def keep() -> int:
    """Bundles kept per device (`MEDIA_LOCAL_BACKUP_KEEP`, 7)."""
    return max(1, _int_env("MEDIA_LOCAL_BACKUP_KEEP", 7))


def root() -> Path:
    from agent_media_core._paths import state_dir

    return state_dir() / DIR


def total_bytes() -> int:
    """What every bundle held here takes (backup.py asks, to choose a tier)."""
    d = root()
    if not d.is_dir():
        return 0
    n = 0
    for f in d.glob(f"*/*{EXT}"):
        try:
            n += f.stat().st_size
        except OSError:
            pass
    return n


def _private_dir(p: Path) -> None:
    p.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(p, 0o700)


def _caller(bearer: str) -> tuple[dict | None, dict]:
    """`(device row, {})` for a paired device that is not a guest, else
    `(None, error)`."""
    user, err = auth.gate(bearer)
    if not user:
        return None, err
    if auth.is_guest(bearer):
        return None, dict(GUEST)
    dev = auth._device(bearer)
    if not dev or not DEVICE.fullmatch(str(dev.get("id") or "")):
        return None, {"status": 403, "error": "only a paired device keeps backups here"}
    return {**dev, "role": user.get("role") or "admin"}, {}


def _account(dev: dict) -> str:
    """The signed-in account that enrolled `dev`, "" for a code-paired one."""
    sub = str(dev.get("sub") or "")
    return auth.account_key_of(str(dev.get("iss") or ""), sub) if sub else ""


def _may_see(dev: dict, meta: dict) -> bool:
    if dev.get("role", "admin") == "admin":
        return True
    if meta.get("device") == dev.get("id"):
        return True
    acct = _account(dev)
    return bool(acct) and meta.get("account") == acct


def _param(q: dict, headers, name: str) -> str:
    v = q.get(name)
    if isinstance(v, list):
        v = v[0] if v else ""
    if not v and headers is not None:
        v = headers.get("X-Backup-" + name.replace("_", "-").title()) or ""
    return str(v or "").strip()


def _bad(error: str, **extra) -> tuple[bool, dict, bool]:
    return False, {"error": error, "status": 400, **extra}, False


def save(body: BinaryIO, length: int, q: dict, headers, bearer: str,
         now: float | None = None) -> tuple[bool, dict, bool]:
    """`POST /local/backup`: keep `length` bytes of `body` for the caller's
    device. `(ok, detail, read)`: `read` False when the body was left
    unread (refused first), so the caller closes the connection."""
    dev, err = _caller(bearer)
    if not dev:
        return False, err, False
    now = time.time() if now is None else now
    raw_created = _param(q, headers, "created")
    if not raw_created.isdigit() or not 0 < int(raw_created) <= now + 86400:
        return _bad("created is the time the bundle was made, in epoch seconds")
    created = int(raw_created)
    fmt = _param(q, headers, "format")
    if not FORMAT.fullmatch(fmt):
        return _bad('format names the bundle\'s format, e.g. "sbb1"')
    sha = _param(q, headers, "sha256").lower()
    if not SHA.fullmatch(sha):
        return _bad("sha256 is the body's SHA-256, 64 hex digits")
    raw_plain = _param(q, headers, "size_plain")
    if raw_plain and not raw_plain.isdigit():
        return _bad("size_plain is a number of bytes")
    if length <= 0:
        return False, {"error": "nothing sent (Content-Length is required)", "status": 411}, False
    if length > max_bytes():
        return False, {"error": f"too large (the limit is {max_bytes() >> 20} MB)",
                       "code": "too_big", "limit": max_bytes(), "status": 413}, False

    folder = root() / str(dev["id"])
    try:
        _private_dir(root())
        _private_dir(folder)
    except OSError as e:
        return False, {"error": f"could not keep the backup ({e.strerror or 'mkdir failed'})",
                       "status": 500}, False
    stem = f"{created}-{sha[:8]}"
    part = folder / f".{stem}.{os.getpid()}.{threading.get_ident()}.part"
    digest, left = hashlib.sha256(), length
    try:
        fd = os.open(part, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "wb") as out:
            while left > 0:
                chunk = body.read(min(CHUNK, left))
                if not chunk:
                    break
                digest.update(chunk)
                out.write(chunk)
                left -= len(chunk)
    except OSError as e:
        part.unlink(missing_ok=True)
        return False, {"error": f"could not keep the backup ({e.strerror or 'write failed'})",
                       "status": 500}, True
    if left:
        part.unlink(missing_ok=True)
        return _bad(f"the upload stopped {left} bytes short")[:2] + (True,)
    if digest.hexdigest() != sha:
        part.unlink(missing_ok=True)
        return False, {"error": "the body's SHA-256 is not the sha256 sent: nothing kept",
                       "code": "sha256_mismatch", "status": 400}, True
    meta = {"device": dev["id"], "device_name": str(dev.get("name") or "") or None,
            "account": _account(dev), "created": created, "size": length, "sha256": sha,
            "format": fmt, "size_plain": int(raw_plain) if raw_plain else None,
            "uploaded": round(now, 3)}
    final = folder / (stem + EXT)
    try:
        with _LOCK:
            os.replace(part, final)
            os.chmod(final, 0o600)
            mfd = os.open(folder / (stem + ".json"), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(mfd, "w") as m:
                json.dump(meta, m)
            kept = _prune(folder)
    except OSError as e:
        part.unlink(missing_ok=True)
        return False, {"error": f"could not keep the backup ({e.strerror or 'write failed'})",
                       "status": 500}, True
    return True, {"id": f"{dev['id']}.{stem}", "size": length, "created": created,
                  "kept": kept}, True


def _bundles(folder: Path) -> list[tuple[int, float, str]]:
    """`(created, mtime, stem)` of each bundle in a device's folder."""
    out = []
    for f in folder.glob(f"*{EXT}"):
        stem = f.name[:-len(EXT)]
        if not ID.fullmatch(f"x.{stem}"):
            continue
        try:
            out.append((int(stem.split("-", 1)[0]), f.stat().st_mtime, stem))
        except (OSError, ValueError):
            continue
    return sorted(out, reverse=True)


def _prune(folder: Path) -> int:
    """Delete the device's bundles past the newest `keep()`; how many are left."""
    have = _bundles(folder)
    for _, _, stem in have[keep():]:
        (folder / (stem + EXT)).unlink(missing_ok=True)
        (folder / (stem + ".json")).unlink(missing_ok=True)
    return min(len(have), keep())


def _meta(folder: Path, stem: str) -> dict | None:
    try:
        got = json.loads((folder / (stem + ".json")).read_text())
    except (OSError, ValueError):
        return None
    return got if isinstance(got, dict) else None


def _device_names() -> dict[str, str]:
    from . import devices

    try:
        return {str(d.get("id") or ""): str(d.get("name") or "") for d in devices.list_devices()}
    except Exception:  # noqa: BLE001 — the name kept at upload time stands in
        return {}


def _row(bid: str, meta: dict, names: dict[str, str]) -> dict:
    dev = str(meta.get("device") or "")
    return {"id": bid, "device": dev, "device_name": names.get(dev) or meta.get("device_name") or None,
            "created": meta.get("created"), "size": meta.get("size"), "sha256": meta.get("sha256"),
            "format": meta.get("format"), "size_plain": meta.get("size_plain"),
            "uploaded": meta.get("uploaded")}


def listing(bearer: str) -> tuple[bool, dict]:
    """`GET /local/backups` → `{"backups": [...]}`, newest first, the ones the
    caller may see."""
    dev, err = _caller(bearer)
    if not dev:
        return False, err
    base, out = root(), []
    if base.is_dir():
        names = _device_names()
        for folder in sorted(p for p in base.iterdir() if p.is_dir() and DEVICE.fullmatch(p.name)):
            for _, _, stem in _bundles(folder):
                meta = _meta(folder, stem)
                if meta is None or meta.get("device") != folder.name or not _may_see(dev, meta):
                    continue
                out.append(_row(f"{folder.name}.{stem}", meta, names))
    out.sort(key=lambda r: (r.get("created") or 0, r["id"]), reverse=True)
    return True, {"backups": out, "keep": keep(), "max_bytes": max_bytes()}


def _find(bid: str, bearer: str) -> tuple[bool, dict]:
    dev, err = _caller(bearer)
    if not dev:
        return False, err
    m = ID.fullmatch(bid or "")
    if not m:
        return False, dict(NOT_FOUND)
    folder = root() / m.group(1)
    stem = f"{m.group(2)}-{m.group(3)}"
    file = folder / (stem + EXT)
    meta = _meta(folder, stem)
    if meta is None or meta.get("device") != m.group(1) or not file.is_file() \
            or not _may_see(dev, meta):
        return False, dict(NOT_FOUND)
    return True, {"file": file, "meta": meta, "folder": folder, "stem": stem}


def open_bundle(bid: str, bearer: str) -> tuple[bool, dict]:
    """`GET /local/backup/<id>`: `{"file", "size", "sha256", "name"}` to send."""
    ok, got = _find(bid, bearer)
    if not ok:
        return False, got
    try:
        size = got["file"].stat().st_size
    except OSError:
        return False, dict(NOT_FOUND)
    return True, {"file": got["file"], "size": size, "sha256": got["meta"].get("sha256") or "",
                  "name": f"{bid}{EXT}"}


def delete(bid: str, bearer: str) -> tuple[bool, dict]:
    """`DELETE /local/backup/<id>`."""
    ok, got = _find(bid, bearer)
    if not ok:
        return False, got
    with _LOCK:
        (got["folder"] / (got["stem"] + EXT)).unlink(missing_ok=True)
        (got["folder"] / (got["stem"] + ".json")).unlink(missing_ok=True)
    return True, {"id": bid, "deleted": True}
