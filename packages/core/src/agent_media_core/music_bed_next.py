"""What the next long reply will have around it, picked ahead (David, 20 Nov
2026: "It would be nice to have up next alongside recently played ... and
also for backing music and outros"; agent-mail #1099).

A reply's bed is chosen as the reply starts (music_bed.choose), so there
was nothing to show as "next". Now the first time anyone asks
(GET /speech/music/next, `peek`), the server makes that choice ahead and
keeps it in `music-bed-next.json`; the next reply's bed is that pick
(`take`, called by intake for a reply's bed only), and the next ask picks
again. The list tells the truth because the reply plays what it showed.

- **bed** — the held plan: a favourite's instrumental, a library track,
  generated music, or streaming tracks.
- **sing_out** — the original the bed hands over to after a long reply
  (music_bed_vocal.after_for), when the bed is a favourite with its
  original kept and "Outro music after long replies" is on. It follows the
  bed: a new bed is a new outro.
- **sing_in** — the few seconds of that original ahead of the first word
  (music_bed_vocal.before_for), with its switch on; not before the answer
  to a message just sent, so it is "if".

A held pick is let go, and the reply chooses as before, when it no longer
fits: the choice in Settings or the moment (Auto's mood, calm mode) moved
on (its `music_bed.Pick` differs), its file is gone, or it is older than
HOLD_S. While nobody asks, nothing is held and nothing changed: Match the
music to the topic still weighs each reply's words. Once a pick is shown,
it wins over the topic — the list said what would play.

`skip` drops the pick and picks again (a favourite just picked rests, so it
is a different song); sing_out's skip is the bed's, as the outro is the
bed's song. Nothing here is written to the log of music around speech
(speech_music): only what plays is.
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Optional

from . import failures as _failures

log = logging.getLogger(__name__)

#: A pick older than this is chosen again (the hour's mood is in its Pick,
#: but a favourite's weighting by the listening mood is not).
HOLD_S = 3 * 3600.0

#: How many times a skip tries for a different track before taking the same.
SKIP_TRIES = 4

KINDS = ("bed", "sing_out", "sing_in")


def _path() -> Path:
    from ._paths import state_dir
    return state_dir() / "music-bed-next.json"


def _load(path: Optional[Path] = None) -> Optional[dict]:
    try:
        d = json.loads((path or _path()).read_text())
        return d if isinstance(d, dict) else None
    except (OSError, ValueError):
        return None


def _save(d: Optional[dict], path: Optional[Path] = None) -> None:
    p = path or _path()
    try:
        if d is None:
            p.unlink(missing_ok=True)
            return
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(d))
        os.replace(tmp, p)
    except OSError as e:
        _failures.swallowed(e)


def _plan_to(plan) -> dict:
    return {"kind": plan.kind, "source": plan.source, "style": plan.style,
            "path": str(plan.path) if plan.path else None,
            "tracks": list(plan.tracks or ()), "credit": plan.credit,
            "fallback": plan.fallback}


def _plan_from(d: dict):
    from .music_bed import Plan
    return Plan(str(d.get("kind") or "under"), str(d.get("source") or ""),
                str(d.get("style") or ""),
                path=Path(d["path"]) if d.get("path") else None,
                tracks=tuple(d.get("tracks") or ()), credit=d.get("credit"),
                fallback=str(d.get("fallback") or ""))


def _pick_now():
    """The reply's Pick right now, as intake would ask for a long reply
    (music_bed.pick_for), or None with the bed off."""
    from . import music_bed
    return music_bed.pick_for()


def _fits(held: dict, pick, now: float) -> bool:
    if pick is None or list(pick) != list(held.get("pick") or []):
        return False
    if now - float(held.get("at") or 0) > HOLD_S:
        return False
    plan = held.get("plan") or {}
    p = plan.get("path")
    if plan.get("kind") == "under" and (not p or not Path(p).is_file()):
        return False
    return True


def _choose(pick, avoid: str = ""):
    """A fresh plan for `pick` (music_bed.choose, as if nothing else played
    and with no topic), trying again while it is the track to `avoid`."""
    from . import music_bed, speech_music
    plan = None
    for _ in range(SKIP_TRIES if avoid else 1):
        plan = music_bed.choose(pick, False, None)
        if plan is None or not avoid or speech_music.track_of(plan)["track_key"] != avoid:
            break
    return plan


def _held(now: Optional[float] = None, path: Optional[Path] = None,
          pick="?", fresh: bool = True) -> tuple[Optional[dict], object]:
    """The held pick that still fits (picked now when `fresh` and none
    does), and the Pick it is for."""
    now = time.time() if now is None else now
    pick = _pick_now() if pick == "?" else pick
    held = _load(path)
    if held is not None and _fits(held, pick, now):
        return held, pick
    if held is not None:
        _save(None, path)
    if not fresh or pick is None:
        return None, pick
    plan = _choose(pick)
    if plan is None:
        return None, pick
    held = {"pick": list(pick), "at": now, "plan": _plan_to(plan)}
    _save(held, path)
    return held, pick


def take(pick, target=None, path: Optional[Path] = None):
    """The held plan for this reply's `pick`, let go as it is used; None
    when nothing fitting is held (the reply then chooses as before). A
    streaming plan needs its player free on `target`. Never raises."""
    try:
        held, _ = _held(pick=pick, path=path, fresh=False)
        if held is None:
            return None
        _save(None, path)
        plan = _plan_from(held["plan"])
        if plan.kind == "stream":
            from . import music_bed, music_bed_stream as st
            if st.available(music_bed._target_name(target)) is not None:
                return None
        return plan
    except Exception as e:  # noqa: BLE001 — a bed must never cost a reply
        _failures.swallowed(e)
        return None


# ---- what the app is shown -----------------------------------------------------------------

def _why(plan, pick) -> str:
    from . import music_bed
    parts = []
    try:
        from . import calm
        if calm.active():
            parts.append("calm mode")
    except Exception as e:  # noqa: BLE001
        _failures.swallowed(e)
    if not parts:
        try:
            parts.append(music_bed.auto_now().why)
        except Exception as e:  # noqa: BLE001
            _failures.swallowed(e)
    src = music_bed.SOURCE_LABELS.get(plan.source, plan.source)
    if getattr(pick, "source", "") == "favourites" and plan.source != "favourites":
        src = f"mixed in: {src.lower()}"
    style = music_bed.LABELS.get(plan.style, plan.style)
    head = src if plan.source == "favourites" else f"{src} → {style}"
    return "; ".join([head] + parts)


def _art(vid: str) -> Optional[str]:
    return f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg" if vid else None


def _row(plan, pick) -> dict:
    from . import speech_music
    t = speech_music.track_of(plan)
    credit = plan.credit or {}
    vid = ""
    if plan.source == "favourites":
        vid = str(credit.get("original_id") or "")
    elif plan.source == "streaming" and t["track_key"].startswith("yt:"):
        vid = t["track_key"][3:]
    title = t["title"]
    if plan.source == "favourites":
        title = str(credit.get("title") or title)  # "Instrumental of …"
    elif plan.source == "generated":
        from . import music_bed
        title = music_bed.LABELS.get(plan.style, plan.style)
    out = {"title": title, "artist": t["artist"], "source": t["source"],
           "style": plan.style, "why": _why(plan, pick), "track_key": t["track_key"]}
    art = _art(vid)
    if art:
        out["art"] = art
    return out


def _vocal(plan, which: str) -> Optional[dict]:
    """sing_out's or sing_in's row for a held favourite, or None."""
    if plan.source != "favourites" or not plan.path:
        return None
    from . import music_bed_mix, music_bed_vocal
    try:
        from . import calm
        if calm.active():
            return None
    except Exception as e:  # noqa: BLE001
        _failures.swallowed(e)
    if which == "sing_out":
        if not music_bed_mix.on("vocal"):
            return None
        got = music_bed_vocal.after_for(plan.path)
    else:
        if not music_bed_mix.on("sing_in"):
            return None
        got = music_bed_vocal.before_for(plan.path)
    if not got:
        return None
    credit = plan.credit or {}
    vid = str(credit.get("original_id") or "")
    out = {"title": str(credit.get("original_title") or ""),
           "artist": str(credit.get("creator") or ""), "source": "favourites",
           "style": plan.style,
           "why": ("the original, after a long reply" if which == "sing_out"
                   else "the original, ahead of a long reply, unless it answers "
                        "a message just sent"),
           "track_key": str(got.get("key") or "")}
    art = _art(vid)
    if art:
        out["art"] = art
    return out


def peek(path: Optional[Path] = None, now: Optional[float] = None) -> dict:
    """`{"bed", "sing_out", "sing_in", "reason"}`: what the next long reply
    will have, picked now if nothing fitting is held. Each is a row (title,
    artist, source, style, why, track_key, art?) or None; `reason` says why
    the bed is None ("off": no bed; "none_ready": nothing to play yet), else
    None. Never raises."""
    out: dict = {"bed": None, "sing_out": None, "sing_in": None, "reason": None}
    try:
        held, pick = _held(now=now, path=path)
        if held is None:
            out["reason"] = "off" if pick is None else "none_ready"
            return out
        plan = _plan_from(held["plan"])
        out["bed"] = _row(plan, pick)
        out["sing_out"] = _vocal(plan, "sing_out")
        out["sing_in"] = _vocal(plan, "sing_in")
        out["picked_at"] = held.get("at")
    except Exception as e:  # noqa: BLE001
        log.warning("music-bed next: %s", e)
    return out


def skip(kind: str, path: Optional[Path] = None, now: Optional[float] = None) -> dict:
    """Pick the bed again (sing_out's and sing_in's skip are the bed's),
    away from the track held; what `peek` answers after. ValueError for an
    unknown kind."""
    if kind not in KINDS:
        raise ValueError("kind must be " + ", ".join(KINDS))
    now = time.time() if now is None else now
    pick = _pick_now()
    held = _load(path)
    avoid = ""
    if held is not None:
        try:
            from . import speech_music
            avoid = speech_music.track_of(_plan_from(held["plan"]))["track_key"]
        except Exception as e:  # noqa: BLE001
            _failures.swallowed(e)
    _save(None, path)
    if pick is not None:
        plan = _choose(pick, avoid)
        if plan is not None:
            _save({"pick": list(pick), "at": now, "plan": _plan_to(plan)}, path)
    return peek(path=path, now=now)
