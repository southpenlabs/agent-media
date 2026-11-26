"""A book put in the library folder from the phone, read at once.

The first run's way in (audiobooks sweep 27 Nov 2026, B5: "first run: put a
book in"): the Books tab uploads an m4b or mp3 (`POST /upload`, then
`POST /books {"action": "add"}`, server books.py), and `add` moves it into
book_library's folder and puts its entry in the catalogue now, rather than at
the next scan, so it is listed, with its cover, when the answer comes.
"""

from __future__ import annotations

import shutil
import time
from pathlib import Path
from typing import Optional

from agent_media_core import book_library as lib
from agent_media_core import failures as _failures


class NotABook(ValueError):
    """A file `add` will not put in the library (it is left where it was)."""


def add(src: Path, name: str = "", base: Optional[Path] = None, probe=None) -> dict:
    """Put the audio file `src` in the library folder as a book: its
    catalogue entry, with `path`. `name` is its file name there (else
    `src`'s, its extension kept), never over another file (`name-2.m4b`).

    Raises NotABook, leaving `src` alone, for a kind not in AUDIO_EXTS, a
    file ffprobe finds no length in, or one shorter than MIN_BOOK_S (it would
    never be listed); OSError when the folder cannot be made or written. The
    folder is made when missing, but not its parent: a mount that is away is
    not papered over with an empty folder."""
    base = base or lib.root()
    probe = probe or lib._probe
    ext = src.suffix.lower()
    if ext not in lib.AUDIO_EXTS:
        raise NotABook(f"not an audiobook file (one of {', '.join(sorted(lib.AUDIO_EXTS))})")
    try:
        got = probe(src)
    except Exception as e:  # noqa: BLE001 — ffprobe missing, timed out or unreadable output
        raise NotABook(f"could not read it as audio: {e}") from e
    if got["duration"] <= 0:
        raise NotABook("could not read it as audio")
    if got["duration"] < lib.MIN_BOOK_S:
        raise NotABook(f"shorter than {lib.MIN_BOOK_S // 60} minutes: not listed as a book")
    name = name or src.name
    if Path(name).suffix.lower() != ext:
        name += src.suffix
    if not base.is_dir():
        if not base.parent.is_dir():
            raise OSError(f"the library folder {base} is not there (nor its parent)")
        base.mkdir(exist_ok=True)
    with lib._LOCK:
        stem, suffix = Path(name).stem, Path(name).suffix
        dest, n = base / name, 2
        while dest.exists():
            dest, n = base / f"{stem}-{n}{suffix}", n + 1
        # Under a hidden name until whole: a scan skips dot files, and a move
        # to another disk (a mount) is a copy that takes a while.
        part = base / f".{dest.name}.part"
        shutil.move(str(src), str(part))
        part.replace(dest)
        rel = str(dest.relative_to(base))
        st = dest.stat()
        entry = lib._entry(rel, dest, got, time.time())
        entry["sig"] = f"{st.st_size}:{int(st.st_mtime)}"
        d = lib._load()
        books = dict(d.get("books") or {})
        books[rel] = entry
        # `at` kept: a scan of the whole folder is still due when it was.
        lib._save({**d, "root": str(base), "books": books, "at": d.get("at") or 0})
    if entry.get("cover"):
        try:
            lib._extract_cover(dest, entry["id"])
        except Exception as _exc:  # noqa: BLE001 — listed without its picture
            _failures.swallowed(_exc)
    return {**entry, "path": str(dest)}
