"""The book library: the audiobook folder read by this server itself.

David, 11 Nov 2026: "I'd like to move away from audiobookshelf if we're
still using it for serving sasonica books and podcasts" — "All the way".
Audiobookshelf used to be what knew which files were books, their names
and who read them, and how far he was through each. Now this module reads
the folder (MEDIA_AUDIOBOOK_ABS_DIR, else ~/audiobooks: a file, or a file in
a folder one deep) and keeps what it learns in ``<state_dir>/book-library.json``:

- each file's tags by ffprobe — title (the album, else the title, else the
  file's name), author (album artist, else artist), narrator (composer),
  series (a ``series`` tag, else the folder it sits in), genre — and its
  length, read again only when its size or time changes
- its cover, the picture embedded in the file, kept in ``<cache>/book-covers/``
  and served by a signed link (`cover_url`, ``GET /books/covers/<id>.jpg?sig=``)
- `added`: when the file first appeared here

How far through each is: the book channel's own position (`resume_pos`,
by its path or, for a copy played on the phone, by its file name) over its
length; finished within the last minute or 98%. The folder can be a mount
that is sometimes away (red5's comes from another machine): then the last
reading stands.

A file shorter than MIN_BOOK_S, or one of this server's own spoken renders
(named by date: ``2026-08-09-…``, ``20260603T034856==…``, ``0001 - …``), is
not a book.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import threading
import time
from pathlib import Path
from typing import Optional

from ._paths import cache_dir, state_dir

AUDIO_EXTS = {".m4b", ".m4a", ".mp3", ".mka", ".opus", ".ogg", ".webm", ".flac", ".aac", ".wav"}
MIN_BOOK_S = 5 * 60
_RENDER = re.compile(r"^(\d{4}-\d{2}-\d{2}-|\d{8}T\d{6}==|\d{4} - )")
SCAN_TTL_S = 15 * 60
_LOCK = threading.Lock()
_SCANNING: list = [False]


def root() -> Path:
    return Path(os.path.expanduser(os.environ.get("MEDIA_AUDIOBOOK_ABS_DIR")
                                   or os.environ.get("AUDIOBOOK_LIB") or "~/audiobooks"))


def _store_path() -> Path:
    return state_dir() / "book-library.json"


def _covers() -> Path:
    return cache_dir() / "book-covers"


def _load() -> dict:
    try:
        d = json.loads(_store_path().read_text())
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(d: dict) -> None:
    p = _store_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(f".tmp.{os.getpid()}")
    tmp.write_text(json.dumps(d, ensure_ascii=False))
    tmp.replace(p)


def book_id(rel: str) -> str:
    return "b-" + hashlib.sha1(rel.encode()).hexdigest()[:12]


def _files(base: Path) -> list[Path]:
    out = []
    for p in sorted(base.iterdir()):
        if p.name.startswith("."):
            continue
        if p.is_dir():
            out += [q for q in sorted(p.iterdir()) if q.is_file() and q.suffix.lower() in AUDIO_EXTS
                    and not q.name.startswith(".")]
        elif p.is_file() and p.suffix.lower() in AUDIO_EXTS:
            out.append(p)
    return out


def _probe(path: Path) -> dict:
    r = subprocess.run(["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", "-show_streams",
                        str(path)], capture_output=True, text=True, timeout=120)
    d = json.loads(r.stdout or "{}")
    fmt = d.get("format") or {}
    tags = {k.lower(): v for k, v in (fmt.get("tags") or {}).items()}
    for s in d.get("streams") or []:
        for k, v in (s.get("tags") or {}).items():
            tags.setdefault(k.lower(), v)
    cover = any(s.get("codec_type") == "video" and (s.get("disposition") or {}).get("attached_pic")
                for s in d.get("streams") or [])
    try:
        dur = float(fmt.get("duration") or 0)
    except ValueError:
        dur = 0.0
    return {"tags": tags, "duration": dur, "has_cover": cover}


def _name_title(path: Path) -> str:
    t = re.sub(r"\s*\[[A-Za-z0-9_-]{11}\]$", "", path.stem)  # yt-dlp's [id]
    return re.sub(r"[_]+", " ", t).strip()


def _entry(rel: str, path: Path, got: dict, added: float) -> dict:
    tags = got["tags"]
    folder = rel.split("/", 1)[0] if "/" in rel else ""
    title = (tags.get("album") if folder == "" else tags.get("title")) or tags.get("title") \
        or tags.get("album") or _name_title(path)
    return {"id": book_id(rel), "rel": rel, "title": title.strip(),
            "author": (tags.get("album_artist") or tags.get("artist") or "").strip() or None,
            "narrator": (tags.get("composer") or "").strip() or None,
            "series": (tags.get("series") or tags.get("grouping") or folder or "").strip() or None,
            "genre": (tags.get("genre") or "").strip() or None,
            "duration": got["duration"], "cover": bool(got["has_cover"]), "added": added}


def _extract_cover(path: Path, bid: str) -> None:
    out = _covers() / f"{bid}.jpg"
    if out.exists():
        return
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(f".{out.name}.part.jpg")
    r = subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-i", str(path), "-an",
                        "-map", "0:v:0", "-frames:v", "1", "-vf", "scale='min(600,iw)':-2", str(tmp)],
                       capture_output=True, timeout=120)
    if r.returncode == 0 and tmp.exists() and tmp.stat().st_size:
        tmp.replace(out)
    else:
        tmp.unlink(missing_ok=True)


def scan() -> dict:
    """Read the folder: new and changed files probed, gone ones dropped. The
    catalogue as kept; the last one when the folder is not there."""
    base = root()
    with _LOCK:
        d = _load()
        if not base.is_dir():
            return d
        try:
            files = _files(base)
        except OSError:
            return d
        old = d.get("books") or {}
        books = {}
        for p in files:
            rel = str(p.relative_to(base))
            try:
                st = p.stat()
            except OSError:
                continue
            sig = f"{st.st_size}:{int(st.st_mtime)}"
            prev = old.get(rel)
            if prev and prev.get("sig") == sig:
                books[rel] = prev
                continue
            try:
                got = _probe(p)
            except Exception:  # noqa: BLE001 — unreadable for now; tried next time
                continue
            e = _entry(rel, p, got, (prev or {}).get("added") or st.st_mtime)
            e["sig"] = sig
            if e["cover"]:
                try:
                    _extract_cover(p, e["id"])
                except Exception:  # noqa: BLE001
                    e["cover"] = False
            books[rel] = e
        d = {"at": time.time(), "root": str(base), "books": books}
        _save(d)
        return d


def _catalogue() -> dict:
    """The kept catalogue, a scan started in the background when it is
    stale (the first one done here, so the list is never empty for long)."""
    d = _load()
    if not d.get("books"):
        return scan()
    if time.time() - float(d.get("at") or 0) > SCAN_TTL_S and not _SCANNING[0]:
        def run():
            _SCANNING[0] = True
            try:
                scan()
            finally:
                _SCANNING[0] = False
        threading.Thread(target=run, daemon=True, name="book-scan").start()
    return d


def path_of(e: dict) -> Path:
    return root() / e["rel"]


def items(min_s: float = MIN_BOOK_S) -> list[dict]:
    """The books whose file is here, each with `path`."""
    out = []
    for e in (_catalogue().get("books") or {}).values():
        p = path_of(e)
        if float(e.get("duration") or 0) < min_s or not p.exists() \
                or (min_s and _RENDER.match(os.path.basename(e["rel"]))):
            continue
        out.append({**e, "path": str(p)})
    return out


def by_name() -> dict:
    """Every file the catalogue knows (clips too), by its file name: for
    naming a play the history holds by its path."""
    return {os.path.basename(e["rel"]): {**e, "path": str(path_of(e))}
            for e in (_catalogue().get("books") or {}).values()}


def find(bid: str) -> Optional[dict]:
    return next((e for e in items(0) if e["id"] == bid), None)


def progress(e: dict, positions: dict) -> dict:
    """``{progress 0–1, finished, at}`` for a book from `positions`
    (`resume_positions()`), by its path or file name."""
    got = positions.get(e["path"]) or positions.get("name:" + os.path.basename(e["path"]))
    dur = float(e.get("duration") or 0)
    if not got or dur <= 0:
        return {"progress": 0.0, "finished": False, "at": 0.0}
    pos_s = got[0] / 1000.0
    frac = max(0.0, min(1.0, pos_s / dur))
    return {"progress": frac, "finished": frac >= 0.98 or dur - pos_s <= 60, "at": got[1]}


def resume_positions(store=None) -> dict:
    """`{path or "name:<file>": (pos_ms, updated_at)}` from the book
    channel's positions, the newest for a name."""
    from .state import StateStore

    st = store or StateStore()
    out: dict = {}
    for uri, pos, at in st.all_resume_pos():
        out[uri] = (pos, at)
        k = "name:" + os.path.basename(uri)
        if k not in out or out[k][1] < at:
            out[k] = (pos, at)
    return out


# ---- covers ---------------------------------------------------------------------

def _sig(name: str) -> str:
    from . import clip_sign
    return clip_sign.sign("book-cover:" + name)


def cover_url(e: dict) -> Optional[str]:
    """The cover's signed web address on the public server, or None."""
    if not e.get("cover"):
        return None
    public = os.environ.get("MEDIA_PUBLIC_URL", "").strip().rstrip("/")
    if not public:
        return None
    name = f"{e['id']}.jpg"
    return f"{public}/books/covers/{name}?sig={_sig(name)}"


def served_cover(name: str, sig: str) -> Optional[Path]:
    import hmac
    if not re.fullmatch(r"b-[0-9a-f]{12}\.jpg", name or "") or not sig \
            or not hmac.compare_digest(_sig(name), sig):
        return None
    p = _covers() / name
    return p if p.is_file() else None
