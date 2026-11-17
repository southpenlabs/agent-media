"""The file browser, read only: a folder's files, and opening one.

docs/proposals/2026-11-17-file-browser-and-folder-permissions.md, step 2
(David, 17 Nov 2026: "Go on to step 2"; our own code, not a second file
server: "Own code, copyparty for uploads later"). The app opens it from a
folder in a chat's path row or a project's page.

  GET  /files?dir=<folder>                     → the folder's entries
  GET  /files?project=<name>[&path=<rel>]      → the same, from a project
  GET  /files/read?path=<file>[&text=1|&download=1]  → the file
  POST /files/link {"path"}                    → a signed link to it

**What may be browsed** is what a reply's file paths may open (§6.18a,
session_files): the projects roots and their projects, project folders of
their own, the doc roots and the upload root, with nothing hidden, secret
or a key store on the way and no file that is a secret by its name. The
home folder and anything above a root are not browsable. A guest is
refused (as for thread files) unless an admin has granted it folders; a
device limited to its folders (folder_grants.py) lists a folder only where
it may `browse` or where the way leads down to a grant, sees only those
entries, and opens a file only where it may `open` (`download` for its
bytes as a file or a link).
"""

from __future__ import annotations

import os
import stat
import time
from pathlib import Path

from . import auth, folder_grants, session_files

#: The most entries one folder answers.
MAX_ENTRIES = 2000


def _bases() -> list[Path]:
    from . import doc_read, uploads

    out: list[Path] = []
    for b in [*doc_read.roots(), *doc_read.single_projects(),
              session_files._resolved(uploads.root())]:
        if b is not None and b not in out and not session_files._too_broad(b):
            out.append(b)
    return out


def _base_of(real: Path, bases: list[Path]) -> Path | None:
    """The browsable folder `real` (resolved) is in, the deepest one, or
    None. `real` may be the base itself."""
    if session_files._denied(real):
        return None
    best = None
    for base in bases:
        try:
            rel = real.relative_to(base)
        except ValueError:
            continue
        if session_files._hidden_or_secret(rel):
            continue
        if best is None or len(base.parts) > len(best.parts):
            best = base
    return best


def _gate(bearer: str) -> tuple[dict | None, dict]:
    user, err = auth.gate(bearer)
    if not user:
        return None, err
    dev = auth._device(bearer)
    if auth.is_guest(bearer) and not folder_grants.scoped(dev):
        return None, {"error": "a guest device does not open files", "status": 403,
                      "code": "guest"}
    return dev or {}, {}


def _missing() -> dict:
    return {"error": "not a folder this device may browse", "status": 404}


def _light_kind(p: Path) -> str:
    """How the app opens a listed file, from its name alone (a listing does
    not read every file): doc, text, image, video, audio, pdf or file.
    /files/read's `kind` is the exact one."""
    ext = p.suffix.lower().lstrip(".")
    if ext in session_files.DOC:
        return "doc"
    for kind, exts in (("image", session_files.IMAGE), ("video", session_files.VIDEO),
                       ("audio", session_files.AUDIO)):
        if ext in exts:
            return kind
    if ext == "pdf":
        return "pdf"
    if ext in session_files.TEXT or p.name.lower() in session_files.TEXT_NAMES:
        return "text"
    return "file"


def _project_of(real: Path) -> tuple[str, str]:
    """(name, folder) of the project `real` is in, or ("", "")."""
    from agent_media_core import layout

    by_path = layout._index()["by_path"]
    for p in [real, *real.parents]:
        name = by_path.get(str(p))
        if name:
            return name, str(p)
    return "", ""


def _folder(args: dict) -> Path | None:
    raw_dir = str(args.get("dir") or "").strip()
    if raw_dir:
        if raw_dir.startswith("~") and not raw_dir.startswith("~/") and raw_dir != "~":
            return None
        return session_files._resolved(raw_dir)
    project = str(args.get("project") or "").strip()
    if not project:
        return None
    place = folder_grants.canonical(project)
    paths = folder_grants.place_paths(place) if place else []
    if not paths:
        return None
    rel = str(args.get("path") or "").strip().strip("/")
    if rel and (os.path.isabs(rel) or ".." in Path(rel).parts):
        return None
    return session_files._resolved(paths[0] / rel if rel else paths[0])


def listing(args: dict, bearer: str) -> tuple[bool, dict]:
    """`GET /files`: a folder's entries, folders first."""
    dev, err = _gate(bearer)
    if dev is None:
        return False, err
    if not (args.get("dir") or args.get("project")):
        return False, {"error": "dir or project is needed", "status": 400}
    real = _folder(args)
    bases = _bases()
    if real is None or not real.is_dir() or not os.access(real, os.R_OK | os.X_OK):
        return False, _missing()
    base = _base_of(real, bases)
    if base is None:
        return False, _missing()
    here = folder_grants.may(dev, real, "browse")
    if not here and not folder_grants.leads_to(dev, real):
        return False, _missing()

    entries: list[dict] = []
    truncated = False
    try:
        names = sorted(os.listdir(real), key=str.lower)
    except OSError:
        return False, _missing()
    for name in names:
        if name.startswith(".") and name not in session_files.ALLOWED_HIDDEN:
            continue
        low = name.lower()
        if "secret" in low or low in session_files.DENIED_PARTS:
            continue
        p = real / name
        try:
            target = p.resolve(strict=True)
            st = os.stat(target)
        except (OSError, RuntimeError, ValueError):
            continue
        if stat.S_ISDIR(st.st_mode):
            if _base_of(target, bases) is None:
                continue
            if not (folder_grants.may(dev, target, "browse")
                    or folder_grants.leads_to(dev, target)):
                continue
            entries.append({"name": name, "path": str(target), "dir": True, "kind": "dir",
                            "mtime": st.st_mtime})
        elif stat.S_ISREG(st.st_mode):
            if not here or not session_files.allowed(target, bases):
                continue
            if not folder_grants.may(dev, target, "browse"):
                continue
            entries.append({"name": name, "path": str(target), "dir": False,
                            "kind": _light_kind(target), "size": st.st_size,
                            "mtime": st.st_mtime})
        if len(entries) >= MAX_ENTRIES:
            truncated = True
            break
    entries.sort(key=lambda e: (not e["dir"], e["name"].lower()))

    crumbs = []
    for p in [*reversed(real.parents), real]:
        if p == base or p.is_relative_to(base):
            crumbs.append({"name": p.name or str(p), "path": str(p)})
    parent = str(real.parent) if real != base else None
    if parent and not (folder_grants.may(dev, real.parent, "browse")
                       or folder_grants.leads_to(dev, real.parent)):
        parent = None
    project, root = _project_of(real)
    return True, {"dir": str(real), "name": real.name, "base": str(base), "parent": parent,
                  "crumbs": crumbs, "project": project or None, "root": root or None,
                  "entries": entries, "truncated": truncated,
                  "may": folder_grants.perms_at(dev, real) if here else []}


def _file(raw: str, dev: dict, permission: str) -> Path | None:
    raw = (raw or "").strip()
    if not raw or len(raw) > session_files.MAX_RAW or "\x00" in raw:
        return None
    if raw.startswith("~") and not raw.startswith("~/"):
        return None
    try:
        real = Path(os.path.expanduser(raw)).resolve(strict=True)
        st = os.stat(real)
    except (OSError, RuntimeError, ValueError):
        return None
    if not stat.S_ISREG(st.st_mode) or not os.access(real, os.R_OK):
        return None
    if not session_files.allowed(real, _bases()):
        return None
    if not folder_grants.may(dev, real, permission):
        return None
    return real


def open_file(raw: str, bearer: str, permission: str = "open",
              signed: bool = False) -> tuple[bool, dict]:
    """`GET /files/read`: the file, as `{file, size, type, name, kind,
    active}` for the caller to stream. `signed`: a link checked instead of
    the bearer (it was checked when made)."""
    if signed:
        dev = {}
    else:
        dev, err = _gate(bearer)
        if dev is None:
            return False, err
    real = _file(raw, dev, permission)
    if real is None:
        return False, {"error": "not a file this device may open", "status": 404}
    kind = session_files.kind_of(real)
    return True, {"file": real, "size": real.stat().st_size,
                  "type": session_files.content_type(real, kind), "name": real.name,
                  "kind": kind, "active": real.suffix.lower().lstrip(".") in session_files.ACTIVE}


def read_text(raw: str, bearer: str) -> tuple[bool, dict]:
    """`GET /files/read?…&text=1`: as /session/file's text answer."""
    ok, got = open_file(raw, bearer)
    if not ok:
        return False, got
    real: Path = got["file"]
    try:
        with real.open("rb") as f:
            data = f.read(session_files.MAX_TEXT + 1)
    except OSError as e:
        return False, {"error": f"could not read it ({e})", "status": 500}
    binary = b"\0" in data[:8000]
    return True, {"path": str(real), "name": real.name, "size": got["size"],
                  "kind": got["kind"],
                  "text": "" if binary else data[:session_files.MAX_TEXT].decode("utf-8", errors="replace"),
                  "binary": binary, "truncated": len(data) > session_files.MAX_TEXT}


def _sig(path: str, exp: int) -> str:
    import hashlib
    import hmac

    from agent_media_core import clip_sign

    return hmac.new(clip_sign._key(), f"files:{path}:{exp}".encode(),
                    hashlib.sha256).hexdigest()[:40]


def link(body: dict, bearer: str, now: float | None = None) -> tuple[bool, dict]:
    """`POST /files/link {"path"}`: a link that opens the one file with no
    bearer for session_files.LINK_S (needs `download` there)."""
    from urllib.parse import urlencode

    ok, got = open_file(str((body or {}).get("path") or ""), bearer, permission="download")
    if not ok:
        return False, got
    real = str(got["file"])
    exp = int((now if now is not None else time.time()) + session_files.LINK_S)
    q = urlencode({"path": real, "exp": exp, "sig": _sig(real, exp)})
    return True, {"url": "/files/read?" + q, "expires": exp, "type": got["type"],
                  "name": got["name"], "kind": got["kind"]}


def signed_ok(path: str, exp: str, sig: str, now: float | None = None) -> bool:
    import hmac

    try:
        when = int(exp)
    except ValueError:
        return False
    if when < (now if now is not None else time.time()) or not sig:
        return False
    return hmac.compare_digest(_sig(path, when), sig)
