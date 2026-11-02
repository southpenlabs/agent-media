"""The upload root, and putting a file in it (§6.18).

The app draws a `Shared file: <path>` line as a chip and fetches the file
through `GET /upload`, which serves only what is under the upload root
(MEDIA_UPLOAD_DIR, else ~/shared). `media attach` and the server's linking of
paths an agent names in a reply (agent_media_server/file_links.py) both put a
file there the same way: into today's folder, never over another, a file
already under the root left where it is.
"""

from __future__ import annotations

import datetime as dt
import os
import shutil
from pathlib import Path


def root() -> Path:
    return Path(os.path.expanduser(os.environ.get("MEDIA_UPLOAD_DIR") or "~/shared"))


def inside(p: Path) -> bool:
    """Whether `p`, symlinks resolved, is under the upload root."""
    try:
        return p.resolve().is_relative_to(root().resolve())
    except (OSError, RuntimeError, ValueError):
        return False


def share(src: Path, today: dt.date | None = None) -> Path:
    """Where the app can fetch `src`: itself when already under the root,
    else a copy in today's folder (`shot.png`, then `shot-2.png`, …).
    `src` must be a regular file; OSError when the copy fails."""
    p = Path(src).expanduser()
    if inside(p):
        return p.resolve()
    folder = root() / (today or dt.date.today()).isoformat()
    folder.mkdir(parents=True, exist_ok=True)
    stem, ext = p.stem.lstrip(".") or "shared", p.suffix
    dest, n = folder / f"{stem}{ext}", 2
    while dest.exists():
        dest, n = folder / f"{stem}-{n}{ext}", n + 1
    shutil.copy2(p, dest)
    return dest
