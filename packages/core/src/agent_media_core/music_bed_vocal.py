"""Singing it out after a long reply (roadmap 39; David, 31 Oct 2026: "when
the speech finishes, the music keeps playing for a while, but not just as
an instrumental" — "Just the chorus", "Long replies only").

When a LONG reply (music_bed.DEFAULT_MIN_S / DEFAULT_MIN_SENTENCES — 45 s
or 12 sentences — whatever MEDIA_MUSIC_BED_MIN_S says, which is 0 on a host
that puts a bed under every reply) had one of his favourites under it
(music_bed_favourites: an official or karaoke instrumental, or Demucs's
no_vocals of the original), the voice's end hands over to the original
song, vocals and all:

1. Crossfaded (equal power) into the original: a separated bed is the same
   recording, so from the bed's own position over
   MEDIA_MUSIC_BED_VOCAL_XFADE_MS (2000); an official or karaoke version is
   not time-aligned, so from the same relative position snapped to the
   start of the nearest section within MEDIA_MUSIC_BED_VOCAL_SNAP_S (8),
   over MEDIA_MUSIC_BED_VOCAL_LOOSE_XFADE_MS (3000) (`start_pos`).
2. At a music level, not the bed's: the original is kept loudness
   normalised to MEDIA_MUSIC_BED_VOCAL_LUFS (-16), so the crossfade is the
   ramp up.
3. On to the end of the next chorus at least MEDIA_MUSIC_BED_VOCAL_AHEAD_S
   (15) ahead, else the next section boundary that far ahead, else
   MEDIA_MUSIC_BED_VOCAL_FALLBACK_S (30) on; then faded out over
   MEDIA_MUSIC_BED_VOCAL_FADE_MS (3000) (`choose_end`).

The originals are prepared by the background process (music_bed.warm, when
favourites are the source; `upkeep`, MEDIA_MUSIC_BED_VOCAL_PER_PASS a pass,
2): fetched as the favourites are (the rooms cache, on the phone's address),
cut and trimmed as their beds are (so a separated bed lines up), normalised,
analysed once for sections (music_bed_sections.py, run with the Demucs venv's
Python or MEDIA_MUSIC_BED_VOCAL_PYTHON: it needs numpy, never a dependency
here; without it, no sections and the fallback length), and kept as private
clips `music-bed-vocal-<key>.mp3` under MEDIA_MUSIC_BED_VOCAL_MAX_MB (300),
indexed in `music-bed-vocal.json`; dropped when its favourite goes.

Told to a player in the bed's mix (music_bed_mix.params `after`): the
original's file (a clip URI for a phone), whether it is aligned, both
lengths, the bounds and choruses, and the times. The phone does the rest in
the app; this host in under_mix.Handover. A switch in Settings → Music,
"Sing it out after long replies" (`vocal`, on). It replaces the ending and
the carry for that reply; a new reading's under sound ends it quickly, as
does a cut.
"""

from __future__ import annotations

import json
import logging
import math
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

DEFAULTS = {"LUFS": -16.0, "XFADE_MS": 2000.0, "LOOSE_XFADE_MS": 3000.0,
            "SNAP_S": 8.0, "AHEAD_S": 15.0, "FALLBACK_S": 30.0, "FADE_MS": 3000.0,
            "MAX_MB": 300.0, "PER_PASS": 2.0}
#: Where a separated bed is cut (music_bed_favourites._cut's default) and
#: every bed is trimmed (music_bed_library.normalise): the original is cut
#: the same so a separated bed's positions are its own.
TRIM_AF = "silenceremove=start_periods=1:start_duration=0.2:start_threshold=-50dB"


def tunable(name: str) -> float:
    d = DEFAULTS[name]
    try:
        return float(os.environ.get(f"MEDIA_MUSIC_BED_VOCAL_{name}", "") or d)
    except ValueError:
        return d


def is_long(est_s: Optional[float], n_sentences: int) -> bool:
    """A long reply by the default thresholds, whatever the env's minimum."""
    from . import music_bed as B
    if est_s is not None and est_s >= B.DEFAULT_MIN_S:
        return True
    return n_sentences >= B.DEFAULT_MIN_SENTENCES


# ---- the choices at the handover ----------------------------------------------------------

def start_pos(inst_pos: float, inst_s: float, orig_s: float, aligned: bool,
              bounds: Optional[list] = None, snap_s: Optional[float] = None) -> float:
    """Where in the original to come in from the bed at `inst_pos`: the same
    point when aligned (a separated bed); else the same fraction of the
    song, moved to the nearest section start (or the top) within `snap_s`."""
    snap = tunable("SNAP_S") if snap_s is None else snap_s
    orig_s = max(0.0, float(orig_s or 0))
    if orig_s <= 0:
        return 0.0
    pos = max(0.0, float(inst_pos or 0))
    if aligned:
        at = pos
    else:
        frac = (pos / inst_s) if inst_s and inst_s > 0 else 0.0
        at = (frac % 1.0) * orig_s
        near = min([0.0] + [float(b) for b in (bounds or [])], key=lambda b: abs(b - at))
        if abs(near - at) <= snap:
            at = near
    return min(at, max(0.0, orig_s - 1.0))


def choose_end(pos: float, orig_s: float, bounds: Optional[list] = None,
               choruses: Optional[list] = None, ahead_s: Optional[float] = None,
               fallback_s: Optional[float] = None) -> float:
    """When to start fading out, coming in at `pos`: the end of the next
    chorus at least `ahead_s` on, else the next boundary that far on, else
    `fallback_s` on; never past the song's end."""
    ahead = tunable("AHEAD_S") if ahead_s is None else ahead_s
    fall = tunable("FALLBACK_S") if fallback_s is None else fallback_s
    floor = pos + ahead
    ends = sorted(float(c[1]) for c in (choruses or []) if len(c) > 1)
    end = next((e for e in ends if e >= floor), None)
    if end is None:
        end = next((float(b) for b in sorted(bounds or []) if float(b) >= floor), None)
    if end is None:
        end = pos + fall
    return min(end, float(orig_s)) if orig_s and orig_s > 0 else end


def equal_power(t: float) -> tuple[float, float]:
    """(bed, song) gains t (0..1) into the crossfade: cos and sin, so the
    sum of their powers stays 1."""
    t = min(1.0, max(0.0, t))
    return math.cos(t * math.pi / 2), math.sin(t * math.pi / 2)


# ---- the kept originals ----------------------------------------------------------------------

def _index_path() -> Path:
    from ._paths import state_dir
    return state_dir() / "music-bed-vocal.json"


def load() -> dict:
    try:
        d = json.loads(_index_path().read_text())
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def save(d: dict) -> None:
    p = _index_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(f".{p.name}.{os.getpid()}")
    tmp.write_text(json.dumps(d))
    os.replace(tmp, p)


def _dir() -> Path:
    from .intake.submit import _audio_dir
    return _audio_dir()


def _file(rec: dict) -> Path:
    return _dir() / rec["file"]


def record_for(bed_path) -> Optional[dict]:
    """The kept original behind favourites bed file `bed_path`, if ready."""
    try:
        name = Path(bed_path).name
        for rec in load().values():
            if rec.get("bed") == name and _file(rec).is_file():
                return rec
    except Exception as e:  # noqa: BLE001
        log.debug("music-bed vocal: %s", e)
    return None


def after_for(bed_path) -> Optional[dict]:
    """The handover for a long reply over favourites bed `bed_path`, as the
    bed's mix carries it (`uri` a path here; the sink makes it a phone's
    clip URI), or None when its original is not kept."""
    rec = record_for(bed_path)
    if rec is None:
        return None
    aligned = bool(rec.get("aligned"))
    return {"uri": str(_file(rec)), "aligned": aligned,
            "inst_s": float(rec.get("inst_s") or 0), "orig_s": float(rec.get("orig_s") or 0),
            "bounds": list(rec.get("bounds") or []), "choruses": list(rec.get("choruses") or []),
            "xfade_ms": tunable("XFADE_MS" if aligned else "LOOSE_XFADE_MS"),
            "fade_ms": tunable("FADE_MS"), "ahead_s": tunable("AHEAD_S"),
            "fallback_s": tunable("FALLBACK_S"), "snap_s": tunable("SNAP_S")}


def _python() -> Optional[str]:
    named = (os.environ.get("MEDIA_MUSIC_BED_VOCAL_PYTHON") or "").strip()
    if named:
        return named
    from . import music_bed_favourites as fav
    exe = fav.demucs_exe()
    if exe:
        py = Path(exe).parent / "python"
        if py.is_file():
            return str(py)
    return None


def sections(path: Path) -> dict:
    """The original's sections (music_bed_sections.py, out of process), or
    {} without a Python that has numpy, or on any failure."""
    py = _python()
    if not py:
        return {}
    script = Path(__file__).with_name("music_bed_sections.py")
    try:
        r = subprocess.run(["nice", "-n", "19", py, "-I", str(script), str(path)],
                           stdin=subprocess.DEVNULL, capture_output=True, timeout=600)
        if r.returncode != 0:
            log.info("music-bed vocal: sections failed: %s",
                     r.stderr.decode(errors="replace")[-300:])
            return {}
        d = json.loads(r.stdout.decode().strip().splitlines()[-1])
        return d if isinstance(d, dict) else {}
    except (OSError, subprocess.SubprocessError, ValueError, IndexError) as e:
        log.info("music-bed vocal: sections: %s", e)
        return {}


def _seconds(path: Path) -> float:
    try:
        r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                            "-of", "default=nw=1:nk=1", str(path)],
                           stdin=subprocess.DEVNULL, capture_output=True, timeout=60)
        return float(r.stdout.decode().strip() or 0)
    except (OSError, subprocess.SubprocessError, ValueError):
        return 0.0


def normalise(src: Path, dst: Path, separated: bool) -> bool:
    """The original as the handover plays it: trimmed and (for a separated
    bed) cut as its bed was, loudnorm to MEDIA_MUSIC_BED_VOCAL_LUFS."""
    from . import music_bed_favourites as fav
    from . import music_bed_library as lib
    keep = min(lib.KEEP_S, int(fav._num_env("MEDIA_MUSIC_BED_FAVOURITES_KEEP_S",
                                            fav.DEFAULT_KEEP_S))) if separated else lib.KEEP_S
    af = f"{TRIM_AF},loudnorm=I={tunable('LUFS'):g}:TP=-1.5:LRA=11"
    tmp = dst.with_name(f".{dst.stem}.{os.getpid()}.mp3")
    try:
        r = subprocess.run(["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
                            "-i", str(src), "-vn", "-t", str(keep), "-af", af, "-ar", "48000",
                            "-ac", "2", "-c:a", "libmp3lame", "-q:a", "4", str(tmp)],
                           stdin=subprocess.DEVNULL, capture_output=True, timeout=900)
        if r.returncode != 0 or not tmp.is_file() or tmp.stat().st_size < 1024:
            return False
        os.replace(tmp, dst)
        return True
    except (OSError, subprocess.SubprocessError):
        return False
    finally:
        tmp.unlink(missing_ok=True)


def _bytes(d: dict) -> int:
    n = 0
    for rec in d.values():
        try:
            n += _file(rec).stat().st_size
        except (OSError, KeyError):
            pass
    return n


def due() -> bool:
    """Whether a kept favourite has no original kept yet (or one at another
    level). Never fetches."""
    try:
        from . import music_bed_favourites as fav
        if not fav.enabled():
            return False
        have = load()
        want = tunable("LUFS")
        return any(e.get("key") not in have or have[e["key"]].get("lufs") != want
                   for e in fav.tracks(None, fav.load()) if e.get("original_id"))
    except Exception:  # noqa: BLE001
        return False


def upkeep(limit: Optional[int] = None) -> int:
    """The background pass: originals for kept favourites that have none
    (up to `limit`), the gone ones dropped, the cap kept. How many came."""
    from . import music_bed_favourites as fav
    limit = int(tunable("PER_PASS")) if limit is None else limit
    data = fav.load()
    beds = {e["key"]: e for e in fav.tracks(None, data) if e.get("key")}
    d = load()
    for key in [k for k in d if k not in beds]:
        try:
            _file(d[key]).unlink(missing_ok=True)
        except (OSError, KeyError):
            pass
        d.pop(key, None)
    got = 0
    want = tunable("LUFS")
    cap = tunable("MAX_MB") * 1024 * 1024
    for key, e in beds.items():
        if got >= limit or _bytes(d) >= cap:
            break
        if not e.get("original_id") or (key in d and d[key].get("lufs") == want):
            continue
        try:
            local = fav._fetch_local(e["original_id"])
        except Exception as err:  # noqa: BLE001
            log.info("music-bed vocal: fetching %s failed: %s", e["original_id"], err)
            local = None
        if not local or not Path(local).is_file():
            continue
        name = f"music-bed-vocal-{key}.mp3"
        dst = _dir() / name
        dst.parent.mkdir(parents=True, exist_ok=True)
        separated = e.get("method") == "separated"
        if not normalise(Path(local), dst, separated):
            log.info("music-bed vocal: normalising %s failed", key)
            continue
        sec = sections(dst)
        d[key] = {"key": key, "file": name, "bed": e.get("file"), "lufs": want,
                  "aligned": separated, "orig_s": float(sec.get("duration") or _seconds(dst)),
                  "inst_s": _seconds(fav._file(e)),
                  "bounds": sec.get("bounds") or [], "choruses": sec.get("choruses") or [],
                  "made": time.time()}
        save(d)
        got += 1
        log.info("music-bed vocal: kept the original of %s (%d sections)",
                 e.get("title") or key, len(d[key]["bounds"]) + 1)
    save(d)
    return got


if __name__ == "__main__":  # pragma: no cover — a manual pass
    logging.basicConfig(level=logging.INFO)
    print(upkeep(int(sys.argv[1]) if len(sys.argv) > 1 else None))
