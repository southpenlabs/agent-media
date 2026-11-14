"""Each track's key and beat grid, measured once and kept beside its tempo
(docs/proposals/2026-11-14-music-around-speech.md, step four: "Key and a
beat grid measured and stored for each track"; David, 14 Nov 2026: "Start
step four"). Nothing chooses by them yet: step six prefers a neighbouring
key and tempo, step five starts and ends on a beat.

The measuring is music_bed_sections.py's (out of process: it needs numpy,
which agent-media does not depend on), run with the first Python that has
it (`python`): MEDIA_MUSIC_BED_VOCAL_PYTHON, the Demucs venv's, or this
process's own interpreter when numpy is importable there (red2's release
venv has it and no Demucs venv, so the outro's sections were never measured
there before). Without one nothing is measured and nothing is due.

Kept, for each track the background pass (music_bed.warm) looks after:

- a favourite's instrumental and a library track: in its entry's
  ``features`` (with bpm, onsets, energy): ``key``, ``beats``, ``grid_v``;
- an original the outro sings from (music_bed_vocal): on its record, the
  same three, measured with its sections.

``key`` is ``{"tonic", "mode", "name", "camelot", "r"}`` and ``beats``
``{"bpm", "offset", "confidence"}`` (music_bed_sections.py says how), either
null when the file could not be told. ``grid_v`` is the measure's version:
a track measured by an older one (or whose file failed) is not measured
again until the version moves. MEDIA_MUSIC_BED_GRID_PER_PASS (6) tracks a
pass, each a second or two of one niced core.
"""

from __future__ import annotations

import importlib.util
import json
import logging
import os
import subprocess
import sys
from pathlib import Path
from typing import Callable, Iterable, Optional

log = logging.getLogger(__name__)

#: The measure's version: bump it to measure every track again.
VERSION = 1
DEFAULT_PER_PASS = 6


def per_pass() -> int:
    try:
        return max(0, int(os.environ.get("MEDIA_MUSIC_BED_GRID_PER_PASS") or DEFAULT_PER_PASS))
    except ValueError:
        return DEFAULT_PER_PASS


def python() -> Optional[str]:
    """A Python with numpy for the analysis script, or None."""
    named = (os.environ.get("MEDIA_MUSIC_BED_VOCAL_PYTHON") or "").strip()
    if named:
        return named
    from . import music_bed_favourites as fav
    exe = fav.demucs_exe()
    if exe:
        py = Path(exe).parent / "python"
        if py.is_file():
            return str(py)
    try:
        if importlib.util.find_spec("numpy") is not None:
            return sys.executable
    except (ImportError, ValueError):
        pass
    return None


def analyse(path: Path, py: Optional[str] = None) -> dict:
    """music_bed_sections.py's answer for a file: duration, bounds, choruses,
    key, beats; {} without a Python that has numpy, or on any failure."""
    py = py or python()
    if not py:
        return {}
    script = Path(__file__).with_name("music_bed_sections.py")
    try:
        r = subprocess.run(["nice", "-n", "19", py, "-I", str(script), str(path)],
                           stdin=subprocess.DEVNULL, capture_output=True, timeout=600)
        if r.returncode != 0:
            log.info("music-bed grid: analysis of %s failed: %s", path.name,
                     r.stderr.decode(errors="replace")[-300:])
            return {}
        d = json.loads(r.stdout.decode().strip().splitlines()[-1])
        return d if isinstance(d, dict) else {}
    except (OSError, subprocess.SubprocessError, ValueError, IndexError) as e:
        log.info("music-bed grid: %s", e)
        return {}


def needs(rec: Optional[dict]) -> bool:
    """Whether a record (a track's features, an original's record) waits for this measure."""
    return (rec or {}).get("grid_v") != VERSION


def keep(rec: dict, got: dict) -> None:
    """The key and grid of an analysis onto a record, marked with the version
    (a failed file too, so it is not measured again every pass)."""
    rec["key"] = got.get("key") if isinstance(got.get("key"), dict) else None
    rec["beats"] = got.get("beats") if isinstance(got.get("beats"), dict) else None
    rec["grid_v"] = VERSION


def due(entries: Iterable[dict]) -> bool:
    """A bed track waits to be measured, and there is a Python to measure it with."""
    return any(needs(e.get("features")) for e in entries) and python() is not None


def backfill(entries: list[dict], path_of: Callable[[dict], Path],
             limit: Optional[int] = None) -> int:
    """Up to `limit` (per_pass) bed tracks measured into their ``features``;
    how many. A track whose file is gone is left for when it is back."""
    limit = per_pass() if limit is None else limit
    todo = [e for e in entries if needs(e.get("features"))]
    if not todo or limit <= 0:
        return 0
    py = python()
    if not py:
        return 0
    n = 0
    for e in todo:
        if n >= limit:
            break
        try:
            path = path_of(e)
        except (KeyError, TypeError, ValueError):
            continue
        if not path.is_file():
            continue
        keep(e.setdefault("features", {}), analyse(path, py))
        n += 1
    return n


def describe(rec: Optional[dict]) -> str:
    """"A minor (8A) · 92.0 BPM", or "" before it is measured."""
    rec = rec or {}
    if needs(rec):
        return ""
    k, b = rec.get("key") or {}, rec.get("beats") or {}
    parts = []
    if k.get("name"):
        parts.append(f"{k['name']} ({k.get('camelot', '?')})")
    if b.get("bpm"):
        parts.append(f"{b['bpm']:.1f} BPM" + ("" if (b.get("confidence") or 0) >= 0.2 else " (no clear beat)"))
    return " · ".join(parts) or "not told"
