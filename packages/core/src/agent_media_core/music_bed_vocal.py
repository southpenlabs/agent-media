"""Singing it out after a long reply (roadmap 39; David, 31 Oct 2026: "when
the speech finishes, the music keeps playing for a while, but not just as
an instrumental" — "Just the chorus", "Long replies only").

When a LONG reply (music_bed.DEFAULT_MIN_S / DEFAULT_MIN_SENTENCES — 45 s
or 12 sentences — whatever MEDIA_MUSIC_BED_MIN_S says, which is 0 on a host
that puts a bed under every reply; MEDIA_MUSIC_BED_VOCAL_MIN_S and
_MIN_SENTENCES set its own, to sing out after shorter replies) had one of his favourites under it
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
"Outro music after long replies" (`vocal`, on; called Sing it out until 14 Nov 2026). It replaces the ending and
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

# RISE_MS / START_DB: in gently (David, 10 Nov 2026: "Sing it out should ease
# in gently from a lower volume") — the crossfade brings the song to START_DB
# under its full level, then it eases up over RISE_MS (the phone's
# VocalHandover.songEase, a smoothstep).
DEFAULTS = {"LUFS": -16.0, "XFADE_MS": 2000.0, "LOOSE_XFADE_MS": 3000.0,
            "RISE_MS": 6000.0, "START_DB": -12.0,
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
    """A reply long enough to sing out after: the bed's default thresholds
    (45 s or 12 sentences), whatever MEDIA_MUSIC_BED_MIN_S says, unless
    MEDIA_MUSIC_BED_VOCAL_MIN_S / _MIN_SENTENCES set their own (David, 5 Nov
    2026: "let's include a lot more singing out so we can test it").
    Either one reached is enough."""
    min_s, min_n = min_seconds(), min_sentences()
    if est_s is not None and est_s >= min_s:
        return True
    return n_sentences >= min_n


# ---- how long a reply must be: two sliders in Settings → Music ----------------------------
# (David, 5 Nov 2026: "Maybe they could be adjusted in settings in the music
# section" — two sliders, seconds and sentences.) Settings' value, else the
# env, else the bed's default; kept in music-bed.json with the switches.

#: (id, label, hint, min, max, step, unit) of each, in the order Settings lists them.
RANGES = (
    ("vocal_min_s", "Intro and outro after replies of at least",
     "A reply this long when read aloud, or with the sentences below, gets the intro and the outro.",
     0, 120, 5, "s"),
    ("vocal_min_sentences", "Or at least this many sentences",
     "Fewer sentences, and the reply's own length decides.",
     0, 30, 1, "sentences"),
)
RANGE_IDS = tuple(r[0] for r in RANGES)
_ENV = {"vocal_min_s": "MEDIA_MUSIC_BED_VOCAL_MIN_S",
        "vocal_min_sentences": "MEDIA_MUSIC_BED_VOCAL_MIN_SENTENCES"}


def _default(name: str) -> float:
    from . import music_bed as B
    return B.DEFAULT_MIN_S if name == "vocal_min_s" else float(B.DEFAULT_MIN_SENTENCES)


def _stored_num(name: str) -> Optional[float]:
    from . import music_bed
    v = music_bed._stored().get(name)
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _value(name: str) -> float:
    v = _stored_num(name)
    return v if v is not None else _env_num(_ENV[name], _default(name))


def min_seconds() -> float:
    return _value("vocal_min_s")


def min_sentences() -> float:
    return _value("vocal_min_sentences")


def settings() -> list[dict]:
    """The two sliders as GET /speech/music-bed lists them (kind "range")."""
    out = []
    for i, label, hint, lo, hi, step, unit in RANGES:
        v = _value(i)
        out.append({"id": i, "kind": "range", "label": label, "hint": hint,
                    "min": lo, "max": hi, "step": step, "unit": unit,
                    "value": int(v) if float(v).is_integer() else v,
                    "set": _stored_num(i) is not None})
    return out


def check_setting(name: str, value) -> None:
    """ValueError unless `value` suits slider `name` (null forgets it)."""
    for i, _l, _h, lo, hi, _s, _u in RANGES:
        if i == name:
            if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float))
                                      or not lo <= float(value) <= hi):
                raise ValueError(f"{name} must be a number from {lo} to {hi}, or null")
            return
    raise ValueError(f"no music bed setting {name!r}")


def set_stored(name: str, value) -> None:
    from . import music_bed
    check_setting(name, value)
    d = music_bed._stored()
    if value is None:
        d.pop(name, None)
    else:
        d[name] = float(value)
    music_bed._store(d)


def _env_num(name: str, default: float) -> float:
    try:
        v = os.environ.get(name, "").strip()
        return max(0.0, float(v)) if v else default
    except ValueError:
        return default


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
    # The song as the phone shows it, with its controls (David, 31 Oct
    # 2026): like or dislike by `key` (POST /speech/music-bed/song), and
    # radio from `video_id`, the original's YouTube id ("" without one).
    song: dict = {}
    try:
        from . import music_bed_favourites as fav
        song = next((e for e in fav.load()["tracks"] if e.get("key") == rec.get("key")), {})
    except Exception as e:  # noqa: BLE001
        log.debug("music-bed vocal: the song: %s", e)
    return {"uri": str(_file(rec)), "aligned": aligned,
            "title": str(song.get("title") or ""), "artist": str(song.get("artist") or ""),
            "key": str(rec.get("key") or ""), "video_id": str(song.get("original_id") or ""),
            "inst_s": float(rec.get("inst_s") or 0), "orig_s": float(rec.get("orig_s") or 0),
            "bounds": list(rec.get("bounds") or []), "choruses": list(rec.get("choruses") or []),
            "xfade_ms": tunable("XFADE_MS" if aligned else "LOOSE_XFADE_MS"),
            "fade_ms": tunable("FADE_MS"), "ahead_s": tunable("AHEAD_S"),
            "fallback_s": tunable("FALLBACK_S"), "snap_s": tunable("SNAP_S"),
            "rise_ms": max(0.0, tunable("RISE_MS")), "start_db": min(0.0, tunable("START_DB"))}


# ---- singing it in, before a long reply -------------------------------------------------------
# (David, 12 Nov 2026: "Sing it in", the mirror of Sing it out.) Before a long
# reply over a favourite whose original is kept, a few seconds of the original,
# vocals and all, timed to its hook: it fades in from IN_START_DB over
# IN_FADE_MS ("it probably also should fade in at a low volume first"), a
# smoothstep in dB, sings IN_LEN_MS in all (the fade included) and then hands
# over to the bed over IN_OUT_MS as the first word comes: a separated bed at
# the same position (the vocals simply leave), a karaoke or official one at
# the mapped position (`bed_pos`). The phone plays it (Media3Speech, the
# `before` object in the mix), holding its first sentence until the handover;
# this host's own players skip it. A switch, "Sing it in before long replies"
# (`sing_in`, music_bed_mix, off by default); long replies only, by the same
# test and sliders as Sing it out; never before the answer to a message David
# has just sent (`may_sing_in`).

IN_DEFAULTS = {"IN_LEN_MS": 3500.0, "IN_FADE_MS": 1500.0, "IN_START_DB": -18.0,
               "IN_OUT_MS": 1000.0, "IN_OWNER_S": 1800.0}


def in_tunable(name: str) -> float:
    """MEDIA_MUSIC_BED_VOCAL_<name> for Sing it in, else its default."""
    d = IN_DEFAULTS[name]
    try:
        return float(os.environ.get(f"MEDIA_MUSIC_BED_VOCAL_{name}", "") or d)
    except ValueError:
        return d


def in_start(orig_s: float, bounds: Optional[list] = None, choruses: Optional[list] = None,
             fade_s: float = 1.5, len_s: float = 3.5) -> float:
    """Where in the original Sing it in starts: so the first chorus begins as
    the fade in peaks (`fade_s` before it); without choruses, so the first
    section after the top does; without sections, the top (the bed's own
    start). Never so late that the snippet runs off the end."""
    orig_s = max(0.0, float(orig_s or 0))
    starts = sorted(float(c[0]) for c in (choruses or []) if len(c) > 1)
    if not starts:
        starts = sorted(float(b) for b in (bounds or []) if float(b) > 0)
    at = max(0.0, starts[0] - max(0.0, fade_s)) if starts else 0.0
    return max(0.0, min(at, orig_s - len_s - 1.0)) if orig_s > len_s + 1.0 else 0.0


def bed_pos(song_s: float, inst_s: float, orig_s: float, aligned: bool) -> float:
    """Where in the bed the song at `song_s` is (the phone's
    VocalHandover.bedPos): the same point when aligned, else the same
    fraction; never later than a second before the bed's end."""
    at = max(0.0, float(song_s or 0))
    if not aligned and inst_s and inst_s > 0 and orig_s and orig_s > 0:
        at = min(1.0, at / orig_s) * inst_s
    if inst_s and inst_s > 1:
        at = min(at, inst_s - 1.0)
    return max(0.0, at)


def before_for(bed_path) -> Optional[dict]:
    """Sing it in for a long reply over favourites bed `bed_path`, as the
    bed's mix carries it (`before`; `uri` a path here, the sink makes it a
    phone's clip URI), or None when its original is not kept."""
    rec = record_for(bed_path)
    if rec is None:
        return None
    aligned = bool(rec.get("aligned"))
    orig_s, inst_s = float(rec.get("orig_s") or 0), float(rec.get("inst_s") or 0)
    if orig_s <= 1:
        return None
    len_ms = max(500.0, in_tunable("IN_LEN_MS"))
    fade_ms = min(max(0.0, in_tunable("IN_FADE_MS")), len_ms)
    start = in_start(orig_s, rec.get("bounds"), rec.get("choruses"), fade_ms / 1000.0,
                     len_ms / 1000.0)
    return {"uri": str(_file(rec)), "aligned": aligned, "key": str(rec.get("key") or ""),
            "start_s": round(start, 2), "len_ms": len_ms, "fade_in_ms": fade_ms,
            "start_db": min(0.0, in_tunable("IN_START_DB")),
            "out_ms": max(0.0, in_tunable("IN_OUT_MS")),
            "bed_s": round(bed_pos(start + len_ms / 1000.0, inst_s, orig_s, aligned), 2),
            "inst_s": inst_s, "orig_s": orig_s}


def may_sing_in(metadata) -> bool:
    """Whether a reply may be sung in, by what began its turn (the Claude
    Code stop hook's `turn_by` and `turn_at`, hook_claude_code.turn_opener):
    another agent's mail or a harness notice (a background task finishing)
    yes; David's own message — the answer to what he just sent — never,
    unless he sent it more than MEDIA_MUSIC_BED_VOCAL_IN_OWNER_S (30 min)
    before; anything not known (the session host's note, another harness,
    `media say`) never."""
    md = metadata or {}
    by = str(md.get("turn_by") or "")
    if by in ("mail", "notice"):
        return True
    if by == "owner":
        try:
            at = float(md.get("turn_at"))
        except (TypeError, ValueError):
            return False
        return time.time() - at > in_tunable("IN_OWNER_S")
    return False


def _python() -> Optional[str]:
    """A Python with numpy for the analysis (music_bed_grid.python: the one
    named, the Demucs venv's, or this one when it has numpy)."""
    from . import music_bed_grid
    return music_bed_grid.python()


def sections(path: Path) -> dict:
    """The original's sections, key and beat grid (music_bed_sections.py, out
    of process, music_bed_grid.analyse), or {} without a Python that has
    numpy, or on any failure."""
    from . import music_bed_grid
    return music_bed_grid.analyse(path)


def _seconds(path: Path) -> float:
    try:
        r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                            "-of", "default=nw=1:nk=1", str(path)],
                           stdin=subprocess.DEVNULL, capture_output=True, timeout=60)
        return float(r.stdout.decode().strip() or 0)
    except (OSError, subprocess.SubprocessError, ValueError):
        return 0.0


def normalise(src: Path, dst: Path, separated: bool, start=None, end=None) -> bool:
    """The original as the handover plays it: trimmed and (for a separated
    bed) cut as its bed was, loudnorm to MEDIA_MUSIC_BED_VOCAL_LUFS. A song
    of a mix (mix_songs) is its chapter alone, `start` to `end` seconds
    into the mix, so Sing it out sings just that song."""
    from . import music_bed_favourites as fav
    from . import music_bed_library as lib
    keep = min(lib.KEEP_S, int(fav._num_env("MEDIA_MUSIC_BED_FAVOURITES_KEEP_S",
                                            fav.DEFAULT_KEEP_S))) if separated else lib.KEEP_S
    pre, post = fav.segment_args(start, end, keep)
    af = f"{TRIM_AF},loudnorm=I={tunable('LUFS'):g}:TP=-1.5:LRA=11"
    tmp = dst.with_name(f".{dst.stem}.{os.getpid()}.mp3")
    try:
        r = subprocess.run(["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
                            *pre, "-i", str(src), "-vn", *post, "-af", af, "-ar", "48000",
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
        from . import music_bed_grid
        have = load()
        want = tunable("LUFS")
        return (any(e.get("key") not in have or have[e["key"]].get("lufs") != want
                    for e in fav.tracks(None, fav.load()) if e.get("original_id"))
                or (any(music_bed_grid.needs(r) for r in have.values())
                    and music_bed_grid.python() is not None))
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
        seg = () if e.get("original_start") is None else (e["original_start"], e.get("original_end"))
        if not normalise(Path(local), dst, separated, *seg):
            log.info("music-bed vocal: normalising %s failed", key)
            continue
        sec = sections(dst)
        d[key] = {"key": key, "file": name, "bed": e.get("file"), "lufs": want,
                  "aligned": separated, "orig_s": float(sec.get("duration") or _seconds(dst)),
                  "inst_s": _seconds(fav._file(e)),
                  "bounds": sec.get("bounds") or [], "choruses": sec.get("choruses") or [],
                  "made": time.time()}
        if sec:
            from . import music_bed_grid
            music_bed_grid.keep(d[key], sec)
        save(d)
        got += 1
        log.info("music-bed vocal: kept the original of %s (%d sections)",
                 e.get("title") or key, len(d[key]["bounds"]) + 1)
    got += _grid_backfill(d)
    save(d)
    return got


def _grid_backfill(d: dict, limit: Optional[int] = None) -> int:
    """Originals kept before their key and beat grid were measured (step
    four, 14 Nov 2026): a few a pass, sections measured again with them."""
    from . import music_bed_grid
    limit = music_bed_grid.per_pass() if limit is None else limit
    todo = [k for k, r in d.items() if music_bed_grid.needs(r)]
    if not todo or limit <= 0:
        return 0
    py = music_bed_grid.python()
    if not py:
        return 0
    n = 0
    for k in todo:
        if n >= limit:
            break
        rec = d[k]
        try:
            path = _file(rec)
        except (KeyError, TypeError):
            continue
        if not path.is_file():
            continue
        sec = music_bed_grid.analyse(path, py)
        music_bed_grid.keep(rec, sec)
        if sec.get("bounds") is not None and not rec.get("bounds"):
            rec["bounds"], rec["choruses"] = sec.get("bounds") or [], sec.get("choruses") or []
        n += 1
    return n


if __name__ == "__main__":  # pragma: no cover — a manual pass
    logging.basicConfig(level=logging.INFO)
    print(upkeep(int(sys.argv[1]) if len(sys.argv) > 1 else None))
