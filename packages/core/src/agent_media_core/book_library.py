"""The book library: the audiobook folder read by this server itself.

David, 11 Nov 2026: "I'd like to move away from audiobookshelf if we're
still using it for serving sasonica books and podcasts" — "All the way".
Audiobookshelf used to be what knew which files were books, their names
and who read them, and how far he was through each. Now this module reads
the folder (MEDIA_AUDIOBOOK_ABS_DIR, else ~/audiobooks: a file, or a file in
a folder one or two deep, as in Author/Title/files) and keeps what it learns
in ``<state_dir>/book-library.json``:

- each file's tags by ffprobe — title (the album, else the title, else the
  file's name), author (album artist, else artist, else the top folder of
  Author/Title), narrator (composer), series (a ``series`` tag, else the
  folder it sits in) and the book's number in it (`series_part`: a
  ``series-part`` tag, else the title's "Book 6" or "#6";
  media_meta.series_part), genre — and its length, read again only when
  its size or time changes
- a folder whose audio files are all one album (one album tag, or none and
  numbered names) is one book, its parts in order (disc, track, then the
  names as numbers read); a folder of different albums stays separate books
  (sweep 27 Nov 2026, B6). A part keeps its own file and so its old place.
- its cover, the picture embedded in the file, else an image beside it
  (``cover.jpg``, ``folder.jpg``, or one named as the file), kept in
  ``<cache>/book-covers/`` and served by a signed link (`cover_url`,
  ``GET /books/covers/<id>.jpg?sig=``)
- `added`: when the file first appeared here

How far through each is: the book channel's own position (`resume_pos`,
by its path or, for a copy played on the phone, by its file name) over its
length — for a book in parts, the parts before the one last played and the
place in it, over them all; finished within the last minute or 98%. The folder can be a mount
that is sometimes away (red5's comes from another machine): then the last
reading stands.

A book can also be marked by hand (`mark`, sweep 27 Nov 2026, B10): marked
finished, or not finished (one counted finished at 98% that was not), kept
by path in ``<state_dir>/book-marks.json``. A mark stands until the book is
listened to again after it: a newer position decides as before. Starting
over (`start_over`) drops the mark and puts every saved position of the
file (the host's and a phone copy's, by name) back to 0.

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
from agent_media_core import failures as _failures
from agent_media_core import media_meta

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


def _files(base: Path, depth: int = 2) -> list[Path]:
    """The audio files in `base` and its folders up to `depth` deep."""
    out = []
    for p in sorted(base.iterdir()):
        if p.name.startswith("."):
            continue
        if p.is_dir():
            if depth > 0:
                out += _files(p, depth - 1)
        elif p.is_file() and p.suffix.lower() in AUDIO_EXTS:
            out.append(p)
    return out


_COVER_NAMES = ("cover", "folder", "front", "poster")
_IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".webp")


def _side_cover(path: Path, folder_book: bool) -> Optional[Path]:
    """An image beside `path`: one named as the file, or (in its own folder,
    not the library's top) cover.jpg, folder.jpg and the like."""
    try:
        here = {q.name.lower(): q for q in path.parent.iterdir() if q.is_file()}
    except OSError:
        return None
    stems = ([] if folder_book else [path.stem.lower()]) + \
        (list(_COVER_NAMES) if path.parent != root() else [])
    for stem in stems:
        for ext in _IMAGE_EXTS:
            if stem + ext in here:
                return here[stem + ext]
    return None


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


#: The tags a book's place in its series is kept in (Libation's and
#: OpenAudible's ``series-part``, iTunes' movement number).
_PART_TAGS = ("series-part", "series_part", "seriespart", "series_index", "movement")
def _num(v) -> int:
    m = re.match(r"\s*(\d+)", str(v or ""))
    return int(m.group(1)) if m else 0


def _entry(rel: str, path: Path, got: dict, added: float) -> dict:
    tags = got["tags"]
    dirs = rel.split("/")[:-1]
    folder = dirs[-1] if dirs else ""
    title = (tags.get("album") if folder == "" else tags.get("title")) or tags.get("title") \
        or tags.get("album") or _name_title(path)
    series = (tags.get("series") or tags.get("movementname") or tags.get("grouping")
              or folder or "").strip() or None
    author = (tags.get("album_artist") or tags.get("artist") or "").strip() \
        or (dirs[0] if len(dirs) > 1 else "")
    return {"id": book_id(rel), "rel": rel, "title": title.strip(),
            "author": author or None,
            "narrator": (tags.get("composer") or "").strip() or None,
            "series": series,
            "series_part": media_meta.series_part(title, series or "", next(
                (str(tags[k]) for k in _PART_TAGS if str(tags.get(k) or "").strip()), "")),
            "genre": (tags.get("genre") or "").strip() or None,
            "duration": got["duration"], "cover": bool(got["has_cover"]), "added": added,
            # What grouping a folder into one book reads (_group).
            "album": (tags.get("album") or "").strip(), "tag_title": (tags.get("title") or "").strip(),
            "disc": _num(tags.get("disc")), "track": _num(tags.get("track"))}


#: A part's own title that still says only which part it is.
_PART_TITLE = re.compile(r"^\W*(?:(?:chapter|part|track|disc|cd|section|kapitel|cap[ií]tulo)\W*)?\d+\W*$", re.I)


def _natural(name: str) -> list:
    return [(0, int(t), "") if t.isdigit() else (1, 0, t.lower()) for t in re.split(r"(\d+)", name) if t]


def _titled(e: dict) -> bool:
    """Whether a file has a title of its own, not just its part's number."""
    t = e.get("tag_title") or ""
    return bool(t) and not _PART_TITLE.match(t) and t != os.path.splitext(os.path.basename(e["rel"]))[0]


def _one_book(parts: list[dict]) -> bool:
    """Whether the files of one folder are parts of one book: one album tag
    for them all; or none, every name numbered and no part titled as its
    own thing (a folder of episodes is not one book). An m4b is a whole
    book: a folder of them each with its own title stays separate books,
    one album or not (Audible's episodes of a series share one)."""
    if len(parts) < 2:
        return False
    if all(e["rel"].lower().endswith(".m4b") and _titled(e) for e in parts) \
            and len({e["tag_title"] for e in parts}) == len(parts):
        return False
    albums = {e["album"] for e in parts}
    if len(albums) == 1 and "" not in albums:
        return True
    if albums != {""}:
        return False
    return all(re.search(r"\d", os.path.basename(e["rel"])) and not _titled(e) for e in parts)


def _book_of(folder: str, parts: list[dict]) -> dict:
    """One book from the files of `folder`, in order."""
    parts = sorted(parts, key=lambda e: (e["disc"], e["track"], _natural(os.path.basename(e["rel"]))))
    if not all(e["track"] for e in parts):
        parts = sorted(parts, key=lambda e: _natural(os.path.basename(e["rel"])))
    first = parts[0]
    dirs = folder.split("/")
    name = dirs[-1]
    series = next((e["series"] for e in parts if e.get("series") and e["series"] != name), None)
    title = first["album"] or name
    # Its number in the series: a part's (from its tag), else the book's title.
    part = next((e.get("series_part") for e in parts
                 if series and e.get("series") == series and e.get("series_part") is not None), None)
    return {"id": book_id(folder), "rel": folder, "title": title,
            "series_part": part if part is not None else media_meta.series_part(title, series or ""),
            "author": first.get("author") or (dirs[0] if len(dirs) > 1 else None),
            "narrator": first.get("narrator"), "series": series, "genre": first.get("genre"),
            "duration": sum(float(e.get("duration") or 0) for e in parts),
            "cover": False, "added": min(float(e.get("added") or 0) for e in parts),
            "parts": [{"rel": e["rel"], "duration": float(e.get("duration") or 0)} for e in parts],
            "_cover_from": next((e["rel"] for e in parts if e.get("cover")), None)}


def _group(files: dict) -> dict:
    """The books from the files read (`{rel: entry}`): a folder that is one
    book (`_one_book`) as one, every other file its own."""
    by_dir: dict[str, list[dict]] = {}
    for rel, e in files.items():
        if "/" in rel:
            by_dir.setdefault(rel.rsplit("/", 1)[0], []).append(e)
    books: dict = {}
    grouped: set = set()
    for folder, parts in by_dir.items():
        if _one_book(parts):
            books[folder] = _book_of(folder, parts)
            grouped.update(e["rel"] for e in parts)
    for rel, e in files.items():
        if rel not in grouped:
            books[rel] = e
    return books


def _extract_cover(path: Path, bid: str) -> bool:
    """The first picture in `path` (an audio file's embedded one, or an
    image) as the book's cover; whether there is one."""
    out = _covers() / f"{bid}.jpg"
    if out.exists():
        return True
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(f".{out.name}.part.jpg")
    r = subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-i", str(path), "-an",
                        "-map", "0:v:0", "-frames:v", "1", "-vf", "scale='min(600,iw)':-2", str(tmp)],
                       capture_output=True, timeout=120)
    if r.returncode == 0 and tmp.exists() and tmp.stat().st_size:
        tmp.replace(out)
        return True
    tmp.unlink(missing_ok=True)
    return False


def _cover_for(e: dict, base: Path) -> bool:
    """Make `e`'s cover: the picture in its file (a book in parts: the first
    part with one), else an image beside it. Whether it has one."""
    src = e.get("_cover_from") if e.get("parts") else (e["rel"] if e.get("cover") else None)
    try:
        if src and _extract_cover(base / src, e["id"]):
            return True
        side = _side_cover(base / (e["parts"][0]["rel"] if e.get("parts") else e["rel"]),
                           bool(e.get("parts")))
        return side is not None and _extract_cover(side, e["id"])
    except Exception as _exc:  # noqa: BLE001 — no cover this time; tried next scan
        _failures.swallowed(_exc)
        return False


def scan() -> dict:
    """Read the folder: new and changed files probed, gone ones dropped, and
    the files made into books (`_group`). The catalogue as kept; the last
    one when the folder is not there."""
    base = root()
    with _LOCK:
        d = _load()
        if not base.is_dir():
            return d
        try:
            found = _files(base)
        except OSError:
            return d
        # Before folders were books (27 Nov 2026) the files were kept under
        # "books" without what grouping reads: those are read again.
        old = d.get("files") or {}
        was = {**(d.get("books") or {}), **old}
        files = {}
        for p in found:
            rel = str(p.relative_to(base))
            try:
                st = p.stat()
            except OSError:
                continue
            sig = f"{st.st_size}:{int(st.st_mtime)}"
            prev = old.get(rel)
            if prev and prev.get("sig") == sig and "series_part" in prev:
                files[rel] = prev
                continue
            try:
                got = _probe(p)
            except Exception as _exc:  # noqa: BLE001 — unreadable for now; tried next time
                _failures.swallowed(_exc)
                continue
            e = _entry(rel, p, got, (was.get(rel) or {}).get("added") or st.st_mtime)
            e["sig"] = sig
            files[rel] = e
        books = {}
        for rel, e in _group(files).items():
            e = dict(e)
            e["cover"] = _cover_for(e, base)
            e.pop("_cover_from", None)
            books[rel] = e
        d = {"at": time.time(), "root": str(base), "files": files, "books": books}
        _save(d)
        return d


def _catalogue() -> dict:
    """The kept catalogue, a scan started in the background when there is
    none yet or it is stale."""
    d = _load()
    # Never waits for a scan: the folder can be a slow mount (red5's first
    # reading took a minute and a half), and a request must not hang on it.
    if (not d.get("books") or time.time() - float(d.get("at") or 0) > SCAN_TTL_S) \
            and not _SCANNING[0]:
        def run():
            _SCANNING[0] = True
            try:
                scan()
            finally:
                _SCANNING[0] = False
        _SCANNING[0] = True
        threading.Thread(target=run, daemon=True, name="book-scan").start()
    return d


def path_of(e: dict) -> Path:
    """The book's file, or for a book in parts its folder."""
    return root() / e["rel"]


def _placed(e: dict) -> dict:
    """`e` with `path`, and for a book in parts each part's `path` (`parts`)
    and their paths in order (`part_paths`)."""
    out = {**e, "path": str(path_of(e))}
    if e.get("parts"):
        out["parts"] = [{**q, "path": str(root() / q["rel"])} for q in e["parts"]]
        out["part_paths"] = [q["path"] for q in out["parts"]]
    return out


def items(min_s: float = MIN_BOOK_S) -> list[dict]:
    """The books whose file (or folder) is here, each with `path`; a book in
    parts with `parts` and `part_paths` too."""
    out = []
    for e in (_catalogue().get("books") or {}).values():
        p = path_of(e)
        if float(e.get("duration") or 0) < min_s or not p.exists() \
                or (min_s and _RENDER.match(os.path.basename(e["rel"]))):
            continue
        out.append(_placed(e))
    return out


def by_name() -> dict:
    """Every file the catalogue knows (clips too), by its file name: for
    naming a play the history holds by its path. A part names its book
    (whose `path` is the book's folder)."""
    out = {}
    for e in (_catalogue().get("books") or {}).values():
        placed = _placed(e)
        for q in placed.get("parts") or [placed]:
            out[os.path.basename(q["rel"])] = placed
            # And by its whole path, which a part's common name ("01.mp3")
            # needs: look that up first.
            out[q["path"]] = placed
    return out


def book_of_part(path: str) -> Optional[dict]:
    """The book in parts that the file `path` is a part of, or None."""
    e = by_name().get(os.path.normpath(os.path.expanduser(path)))
    return e if e and e.get("parts") else None


def of_path(path: str) -> Optional[dict]:
    """The book in parts whose folder is `path`, or None."""
    want = os.path.normpath(os.path.expanduser(path))
    return next((e for e in items(0) if e.get("parts") and os.path.normpath(e["path"]) == want), None)


def place_in_parts(e: dict, positions: dict) -> tuple[int, float, float]:
    """``(index, pos_s, at)`` in a book in parts: the part played last
    (the newest saved place among them) and the place in it."""
    best = (0, 0.0, 0.0)
    for i, q in enumerate(e["parts"]):
        got = positions.get(q["path"]) or positions.get("name:" + os.path.basename(q["path"]))
        if got and got[1] > best[2]:
            best = (i, got[0] / 1000.0, got[1])
    return best


def find(bid: str) -> Optional[dict]:
    return next((e for e in items(0) if e["id"] == bid), None)


def progress(e: dict, positions: dict, marked: Optional[dict] = None) -> dict:
    """``{progress 0–1, finished, at, marked}`` for a book from `positions`
    (`resume_positions()`), by its path or file name (a book in parts: the
    parts before the one played last and the place in it), and its hand
    mark (`marks()`, read when not given) when no position is newer.
    `marked` is "finished", "unfinished" or None."""
    if e.get("parts"):
        i, at_s, at = place_in_parts(e, positions)
        got = (int((sum(q["duration"] for q in e["parts"][:i]) + at_s) * 1000), at) if at else None
    else:
        got = positions.get(e["path"]) or positions.get("name:" + os.path.basename(e["path"]))
    dur = float(e.get("duration") or 0)
    m = (marks() if marked is None else marked).get(e["path"])
    if m and got and got[1] > float(m.get("at") or 0):
        m = None                                   # listened to since: the position decides
    if m and m.get("state") == "finished":
        return {"progress": 1.0, "finished": True, "at": float(m.get("at") or 0), "marked": "finished"}
    if not got or dur <= 0:
        return {"progress": 0.0, "finished": False, "at": 0.0, "marked": None}
    pos_s = got[0] / 1000.0
    frac = max(0.0, min(1.0, pos_s / dur))
    finished = frac >= 0.98 or dur - pos_s <= 60
    if m and m.get("state") == "unfinished":
        return {"progress": min(frac, 0.99), "finished": False,
                "at": max(got[1], float(m.get("at") or 0)), "marked": "unfinished"}
    return {"progress": frac, "finished": finished, "at": got[1], "marked": None}


# ---- marks by hand ------------------------------------------------------------

MARKS = ("finished", "unfinished")


def _marks_path() -> Path:
    return state_dir() / "book-marks.json"


def marks() -> dict:
    """`{path: {"state": "finished" | "unfinished", "at"}}`."""
    try:
        d = json.loads(_marks_path().read_text())
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_marks(d: dict) -> None:
    p = _marks_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(f".tmp.{os.getpid()}")
    tmp.write_text(json.dumps(d, ensure_ascii=False))
    tmp.replace(p)


def mark(path: str, state: Optional[str], clock=time.time) -> None:
    """Mark the book at `path` finished or not finished; None drops the mark."""
    if state is not None and state not in MARKS:
        raise ValueError(f"mark must be one of {MARKS} or None")
    with _LOCK:
        d = marks()
        if state is None:
            d.pop(path, None)
        else:
            d[path] = {"state": state, "at": clock()}
        _save_marks(d)


def start_over(path: str, store=None) -> int:
    """The book at `path` from the beginning: its mark dropped, and every
    saved position of it (by path, or a copy's by file name) put to 0.
    How many positions were reset."""
    from .state import StateStore

    st = store or StateStore()
    mark(path, None)
    whole = of_path(path)
    # A book in parts: every part, the first last, so it is the part played
    # last (place_in_parts keeps the earlier part on a tie).
    files = list(reversed(whole["part_paths"])) if whole else [path]
    saved = [(u, os.path.basename(u)) for u, _p, _a in st.all_resume_pos()]
    n = 0
    for f in files:
        name = os.path.basename(f)
        for u in [u for u, b in saved if b == name and u != f] + [f]:
            st.set_resume_pos(u, 0)
            n += 1
    return n


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
