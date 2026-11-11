"""Age rendered speech clips out of the state dir.

This was a `find` in a systemd unit of David's:

    find ~/.local/state/agent-media/audio -type f -mtime +7 \
         -not -name 'music-bed-*' -delete

It is product housekeeping — the directory is agent-media's own, and
`_paths.speech_audio_dir()` is the only thing that knows where it is — so it
belongs here, with the exception the `find` had to carry by hand. That
`-not -name` is the whole reason this is code now: `music-bed-*` files are
not clips, they are built artefacts (a favourite with its vocals removed, a
library mix), each costing minutes of demucs and ffmpeg, and aging them out
means rebuilding them. A second exception would have been a second thing to
remember about a shell line nobody reads.

Policy unchanged from that unit: files older than `--days` (7) go, and the
`KEEP` prefixes never do. Folded into the one scheduler as the `audio-prune`
job (docs/proposals/2026-11-12-fewer-services.md §4b).
"""

from __future__ import annotations

import os
import time
from pathlib import Path

from ._paths import speech_audio_dir

#: Name prefixes that are never pruned, whatever their age. Built artefacts,
#: not rendered speech: minutes of demucs and ffmpeg each.
KEEP_PREFIXES = ("music-bed-",)

DEFAULT_DAYS = 7.0


def keep_days() -> float:
    try:
        return float(os.environ.get("MEDIA_AUDIO_KEEP_DAYS") or DEFAULT_DAYS)
    except ValueError:
        return DEFAULT_DAYS


def protected(name: str) -> bool:
    return any(name.startswith(p) for p in KEEP_PREFIXES)


def prune(days: "float | None" = None, *, dry_run: bool = False,
          root: "Path | None" = None, now: "float | None" = None) -> dict:
    """Delete clips older than `days`. Returns what went (or would).

    Only regular files directly in the directory, as the `find` did with
    `-type f`: a subdirectory under here is somebody's, and a symlink is the
    compatibility link `migrate_speech_audio` leaves behind pointing at this
    very directory — following it would walk back in.
    """
    days = keep_days() if days is None else days
    now = time.time() if now is None else now
    cutoff = now - days * 86400
    d = root if root is not None else speech_audio_dir()
    out = {"removed": 0, "bytes": 0, "kept_protected": 0, "days": days}
    try:
        entries = list(d.iterdir())
    except OSError:
        return out
    for p in entries:
        try:
            if p.is_symlink() or not p.is_file():
                continue
            if protected(p.name):
                out["kept_protected"] += 1
                continue
            st = p.stat()
            if st.st_mtime >= cutoff:
                continue
            out["removed"] += 1
            out["bytes"] += st.st_size
            if not dry_run:
                p.unlink()
        except OSError:
            continue
    return out
