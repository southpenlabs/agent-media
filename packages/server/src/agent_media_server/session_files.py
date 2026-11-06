"""File paths a reply names, made tappable in the app (§6.18a).

David, 6 Nov 2026: "Can you make files tappable from there please" — a path
an agent writes in a reply (`docs/proposals/2026-11-06-one-tap-answers.md`,
`packages/server/src/agent_media_server/space_suggest.py`, relative to the
chat's folder or absolute) should open when tapped: a document in the
reader, code as text, anything else handed to the phone.

The app stays generic: it finds the words that look like paths and asks the
server, for one thread, which of them are files it may open. Only those
become links, so "a/b" in prose stays words.

  POST /session/paths {"session", "paths": [<as written>, …]}
      → {"ok", "cwd", "paths": {<as written>: {"path", "name", "size",
         "mtime", "kind", "type", "line"?}}}  (only the ones that resolve)
  GET  /session/file?session=<id>&path=<path>            → the file's bytes
  GET  /session/file?session=<id>&path=<path>&text=1     → {"ok", "path",
         "name", "size", "kind", "text", "binary", "truncated"}
  POST /session/file/link {"session", "path"} → {"ok", "url", "expires",
         "type", "name"}: a signed link that opens the one file with no
         bearer (a download, or the phone's own viewer), for LINK_S.

**Where a path may lead.** A relative path is read against the thread's
folder (`sessions.latest_cwd`), then its checkout's top; `~/` and absolute
paths as they are. The file it names (symlinks resolved) must be inside one
of: the thread's folder or its checkout (unless that is the home folder or
above it), a projects root or a project folder of its own, a doc root
(doc_read.roots), or the upload root (~/shared). Within that folder no part
of the way may be hidden (a dot name, except where screenshots land), named
with "secret", or a key store (.ssh, .gnupg, …); the file itself may not be
a secret by its name (`.env*`, `*.pem`, `*.key`, `id_rsa*`, `credentials*`,
keystores, `auth.json`, `devices.json`, …), and /proc, /sys, /dev and /run
are never served. Only a regular, readable file. Anything else is simply not
in the answer (resolve) or 404 (file).

Guests get none of it (403): a guest reads and listens.
"""

from __future__ import annotations

import mimetypes
import os
import re
import stat
from pathlib import Path

from . import auth

#: The most paths one resolve looks at (a message's worth).
MAX_PATHS = 64
#: Longest path as written that is looked at.
MAX_RAW = 1024
#: How much of a file `&text=1` answers.
MAX_TEXT = 200_000
#: How long a signed link lasts.
LINK_S = 6 * 3600

IMAGE = {"png", "jpg", "jpeg", "gif", "webp", "avif", "heic", "heif", "bmp"}
VIDEO = {"mp4", "m4v", "webm", "mov"}
AUDIO = {"mp3", "wav", "ogg", "opus", "m4a", "aac", "flac"}
#: Opened in the app's document reader when the reader may read it (doc_read).
DOC = {"md", "markdown", "org", "txt"}
#: Shown as text with line numbers.
TEXT = {
    "rst", "adoc", "py", "pyi", "ts", "tsx", "js", "jsx", "mjs", "cjs", "json", "jsonl",
    "json5", "yaml", "yml", "toml", "ini", "cfg", "conf", "sh", "bash", "zsh", "fish",
    "java", "kt", "kts", "gradle", "xml", "html", "htm", "css", "scss", "sass", "less",
    "c", "h", "cc", "cpp", "cxx", "hpp", "rs", "go", "rb", "php", "pl", "lua", "sql",
    "csv", "tsv", "log", "diff", "patch", "svg", "el", "lisp", "clj", "ex", "exs", "erl",
    "hs", "swift", "m", "mm", "r", "jl", "scala", "dart", "vue", "svelte", "tf", "nix",
    "properties", "lock", "service", "timer", "socket", "desktop", "tex", "bib", "proto",
    "graphql", "gql", "srt", "vtt", "ipynb", "cs", "fs", "groovy", "ps1", "bat", "cmake",
    "mk", "dockerfile", "gitignore", "editorconfig",
}
#: Text files known by their whole name.
TEXT_NAMES = {"makefile", "dockerfile", "license", "readme", "notice", "justfile",
              "procfile", "codeowners", "authors", "changelog", "copying", "gemfile",
              "rakefile", "vagrantfile", "containerfile"}
#: Never served from here as themselves: a page or script the server would
#: run from its own origin. They come back as an attachment (and as text).
ACTIVE = {"html", "htm", "xhtml", "svg", "xml", "js", "mjs"}

#: Hidden folders whose files are fine to open (file_links.ALLOWED_HIDDEN).
ALLOWED_HIDDEN = {".playwright-mcp", ".local-previews"}
DENIED_PARTS = {".ssh", ".gnupg", ".password-store", ".aws", ".kube", ".docker"}
DENIED_ROOTS = (Path("/proc"), Path("/sys"), Path("/dev"), Path("/run"))
#: A file that is a secret by its name, wherever it is.
GUARDED = re.compile(
    r"^\.env|\.env$|secret|\.pem$|\.key$|\.p12$|\.pfx$|\.jks$|\.keystore$|\.kdbx$|"
    r"^id_(rsa|dsa|ecdsa|ed25519)|credentials|^auth\.json$|^devices\.json$|"
    r"^\.netrc$|^\.pgpass$|^agent-media\.env$|\.gpg$|\.asc$",
    re.IGNORECASE)
#: A line number after the path: `app.py:42`, `app.py:42:7`, `app.py#L42`.
_LINE = re.compile(r"(?::(\d+)(?::\d+)?|#L(\d+)(?:-L?\d+)?)$")


def _clean(raw: str) -> tuple[str, int | None]:
    """The path as written, without `file://`, quotes and a line number."""
    raw = (raw or "").strip().strip("`'\"")
    if raw.startswith("file://"):
        raw = raw[len("file://"):]
    line = None
    m = _LINE.search(raw)
    if m:
        line = int(m.group(1) or m.group(2))
        raw = raw[:m.start()]
    return raw, line


def _too_broad(p: Path) -> bool:
    """A thread folder that would open too much: `/`, the home folder or
    anything above it."""
    try:
        home = Path.home().resolve()
    except (OSError, RuntimeError):
        return True
    return p == Path("/") or p == home or home.is_relative_to(p)


def _resolved(p: Path | str | None) -> Path | None:
    if not p:
        return None
    try:
        return Path(os.path.expanduser(str(p))).resolve()
    except (OSError, RuntimeError, ValueError):
        return None


def folders(session: str) -> tuple[Path | None, Path | None]:
    """The thread's folder and its checkout's top (either may be None)."""
    from agent_media_core import docs

    from . import sessions

    cwd = _resolved(sessions.latest_cwd(session) or "")
    if cwd is None or not cwd.is_dir():
        return None, None
    top = None
    try:
        got = docs.project_root(cwd)
        top = _resolved(got) if got else None
    except Exception:  # noqa: BLE001 - a git that fails is no checkout
        top = None
    return cwd, top


def bases(cwd: Path | None, top: Path | None) -> list[Path]:
    """The folders a file may be served from, for a thread in `cwd`."""
    from . import doc_read, uploads

    out: list[Path] = []
    for b in [cwd, top]:
        if b is not None and not _too_broad(b) and not _denied(b):
            out.append(b)
    for b in [*doc_read.roots(), *doc_read.single_projects(), _resolved(uploads.root())]:
        if b is not None and b not in out and not _too_broad(b):
            out.append(b)
    return out


def _denied(p: Path) -> bool:
    if any(p == d or p.is_relative_to(d) for d in DENIED_ROOTS):
        return True
    return any("secret" in part.lower() or part.lower() in DENIED_PARTS for part in p.parts[1:])


def _hidden_or_secret(rel: Path) -> bool:
    for part in rel.parts:
        low = part.lower()
        if "secret" in low or low in DENIED_PARTS:
            return True
        if part.startswith(".") and part not in ALLOWED_HIDDEN:
            return True
    return False


def allowed(real: Path, among: list[Path]) -> bool:
    """`real` (resolved) is a file that may be opened from one of `among`."""
    if _denied(real) or GUARDED.search(real.name):
        return False
    for base in among:
        try:
            rel = real.relative_to(base)
        except ValueError:
            continue
        if rel.parts and not _hidden_or_secret(rel):
            return True
    return False


def _candidates(raw: str, cwd: Path | None, top: Path | None) -> list[Path]:
    if raw.startswith("~/") or raw.startswith("/"):
        return [Path(os.path.expanduser(raw))]
    if raw.startswith("~"):
        return []
    out = []
    for b in [cwd, top]:
        if b is not None:
            out.append(b / raw)
    return out


def kind_of(p: Path) -> str:
    """How the app opens it: doc, text, image, video, audio, pdf or file."""
    from . import doc_read

    ext = p.suffix.lower().lstrip(".")
    if ext in DOC:
        return "doc" if doc_read.safe_path(str(p)) else "text"
    if ext in IMAGE:
        return "image"
    if ext in VIDEO:
        return "video"
    if ext in AUDIO:
        return "audio"
    if ext == "pdf":
        return "pdf"
    if ext in TEXT or p.name.lower() in TEXT_NAMES:
        return "text"
    if not ext or ext not in {"apk", "zip", "gz", "tgz", "xz", "bz2", "zst", "7z", "rar",
                              "tar", "jar", "aab", "deb", "rpm", "iso", "img", "bin",
                              "exe", "dmg", "so", "o", "a", "class", "pyc", "db", "sqlite",
                              "woff", "woff2", "ttf", "otf", "docx", "xlsx", "pptx", "odt",
                              "ods", "epub", "mkv", "avi"}:
        # Unknown: text when it reads as text and is not huge.
        try:
            if p.stat().st_size <= 2_000_000:
                with p.open("rb") as f:
                    if b"\0" not in f.read(8000):
                        return "text"
        except OSError:
            pass
    return "file"


def content_type(p: Path, kind: str = "") -> str:
    if p.suffix.lower() == ".apk":
        return "application/vnd.android.package-archive"
    ctype = mimetypes.guess_type(p.name)[0] or (
        "text/plain" if kind in ("text", "doc") else "application/octet-stream")
    if ctype.startswith("text/"):
        ctype += "; charset=utf-8"
    return ctype


def find(raw: str, cwd: Path | None, top: Path | None,
         among: list[Path] | None = None) -> tuple[Path, int | None] | None:
    """The file `raw` names in a thread in `cwd`, when it may be opened."""
    if not raw or len(raw) > MAX_RAW or "\x00" in raw or "\n" in raw:
        return None
    raw, line = _clean(raw)
    if not raw:
        return None
    among = bases(cwd, top) if among is None else among
    for cand in _candidates(raw, cwd, top):
        try:
            real = cand.resolve(strict=True)
            st = os.stat(real)
        except (OSError, RuntimeError, ValueError):
            continue
        if not stat.S_ISREG(st.st_mode) or not os.access(real, os.R_OK):
            continue
        if allowed(real, among):
            return real, line
    return None


def describe(real: Path, line: int | None = None) -> dict:
    st = real.stat()
    kind = kind_of(real)
    out = {"path": str(real), "name": real.name, "size": st.st_size, "mtime": st.st_mtime,
           "kind": kind, "type": content_type(real, kind)}
    if line:
        out["line"] = line
    return out


def _gate(session: str, bearer: str) -> tuple[bool, dict]:
    from . import sessions

    session = (session or "").strip()
    if not sessions._SESSION.fullmatch(session):
        return False, {"error": "not a session id", "status": 400}
    user, err = auth.gate(bearer)
    if not user:
        return False, err
    if auth.is_guest(bearer):
        return False, {"error": "a guest device does not open files", "status": 403,
                       "code": "guest"}
    return True, {}


def resolve(body: dict, bearer: str) -> tuple[bool, dict]:
    """`POST /session/paths`: which of `paths` are files the thread may open."""
    session = str((body or {}).get("session") or "")
    ok, err = _gate(session, bearer)
    if not ok:
        return False, err
    paths = (body or {}).get("paths")
    if not isinstance(paths, list):
        return False, {"error": "paths must be a list", "status": 400}
    cwd, top = folders(session)
    among = bases(cwd, top)
    out: dict[str, dict] = {}
    for raw in paths[:MAX_PATHS]:
        if not isinstance(raw, str) or raw in out:
            continue
        got = find(raw, cwd, top, among)
        if got:
            try:
                out[raw] = describe(*got)
            except OSError:
                continue
    return True, {"cwd": str(cwd) if cwd else "", "paths": out}


def _missing() -> dict:
    return {"error": "not a file this thread may open", "status": 404}


def open_file(session: str, path: str, bearer: str, signed: bool = False) -> tuple[bool, dict]:
    """`GET /session/file`: the file, as `{file, size, type, name, kind, active}`
    (the caller streams it). `signed`: a link `link` made was checked instead."""
    if signed:
        from . import sessions

        if not sessions._SESSION.fullmatch(session or ""):
            return False, {"error": "not a session id", "status": 400}
    else:
        ok, err = _gate(session, bearer)
        if not ok:
            return False, err
    cwd, top = folders(session)
    got = find(path, cwd, top)
    if not got:
        return False, _missing()
    real = got[0]
    try:
        size = real.stat().st_size
    except OSError:
        return False, _missing()
    ext = real.suffix.lower().lstrip(".")
    kind = kind_of(real)
    return True, {"file": real, "size": size, "type": content_type(real, kind), "name": real.name,
                  "kind": kind, "active": ext in ACTIVE}


def read_text(session: str, path: str, bearer: str) -> tuple[bool, dict]:
    """`GET /session/file?…&text=1`: the file as text, for the app's viewer."""
    ok, got = open_file(session, path, bearer)
    if not ok:
        return False, got
    real: Path = got["file"]
    try:
        with real.open("rb") as f:
            data = f.read(MAX_TEXT + 1)
    except OSError as e:
        return False, {"error": f"could not read it ({e})", "status": 500}
    binary = b"\0" in data[:8000]
    return True, {"path": str(real), "name": real.name, "size": got["size"], "kind": got["kind"],
                  "text": "" if binary else data[:MAX_TEXT].decode("utf-8", errors="replace"),
                  "binary": binary, "truncated": len(data) > MAX_TEXT}


def _sig(session: str, path: str, exp: int) -> str:
    import hashlib
    import hmac

    from agent_media_core import clip_sign

    return hmac.new(clip_sign._key(), f"sfile:{session}:{path}:{exp}".encode(),
                    hashlib.sha256).hexdigest()[:40]


def link(body: dict, bearer: str, now: float | None = None) -> tuple[bool, dict]:
    """`POST /session/file/link {session, path}`: a link that opens the one
    file with no bearer, for LINK_S (a browser download, the phone's viewer)."""
    import time
    from urllib.parse import urlencode

    session = str((body or {}).get("session") or "")
    ok, got = open_file(session, str((body or {}).get("path") or ""), bearer)
    if not ok:
        return False, got
    real = str(got["file"])
    exp = int((now if now is not None else time.time()) + LINK_S)
    q = urlencode({"session": session, "path": real, "exp": exp, "sig": _sig(session, real, exp)})
    return True, {"url": "/session/file?" + q, "expires": exp, "type": got["type"],
                  "name": got["name"], "kind": got["kind"]}


def signed_ok(session: str, path: str, exp: str, sig: str, now: float | None = None) -> bool:
    """A link `link` made for this thread and path, not yet expired."""
    import hmac
    import time

    try:
        when = int(exp)
    except ValueError:
        return False
    if when < (now if now is not None else time.time()) or not sig:
        return False
    return hmac.compare_digest(_sig(session, path, when), sig)
