"""The music under a reply, played again when the reply is replayed.

David, 31 Oct 2026: "I heard the backing music the first time but when I
replayed it, there didn't seem to be any backing music." A bed (roadmap 39,
music_bed.py) was started only by submit, as a reply is first spoken; a
replay (`media replay`, the app's per-reply Play and "read from here", the
held-reply toast's Play, `replay-prev` / `replay-at-cursor`) pushed the clips
and nothing under them.

Now submit keeps on the reply's history row what was under it:

- ``extras.bed`` (`record`): the bed as it played — its source, style, the
  file in the clip dir (a generated mood, a library or favourites track),
  Mopidy's tracks for a streaming one, the generated stand-in, whether it
  loops, the tone-kind told to a phone, and which kind of reading it was
  under (reply, people, question, alert);
- ``extras.under_tone``: the tone the reading takes when no bed plays —
  "Air" in its project's key (``reply-E`` …), the people harp, or ``think``
  under a thinking readout.

A replay asks `resolve` what to play under it, with the same rules as submit:
no bed when the music bed is off (or a kind's bed is switched off), when
anything else is playing (`other_audio_now`, bounded by
``MEDIA_MUSIC_BED_PROBE_S``), and never under a thought (which gets its
``think`` tone, as submit gives it). The same track when its file is still
there; a generated mood's file is named for the music level it was rendered
at, so the replay plays the CURRENT level's file of the same mood. A file
pruned since: chosen afresh, as submit would choose now. No bed: the tone the
reading had, as submit falls back to it.

`start` plays the answer through the same `music_bed.start` /
`earcons.play_under` path as submit (`SinkSpeech.play_under`): a phone is told
it as the stored ``under`` properties before the replay's start commands, and
the app takes it as the reply sounds and ends the loop with the reply; this
host's own players get a second mpv, started and faded out by the replay's
follower (`cli.cmd_replay_track`). Never raises.
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Callable, Optional

log = logging.getLogger(__name__)


def record(plan, under: bool, stop, kind: str = "reply") -> Optional[dict]:
    """What to keep on the reply's row (`extras.bed`) for bed `plan`
    (music_bed.Plan) as `music_bed.start` answered (`under`, `stop`), or None
    when nothing played. A streaming plan that fell back to its generated
    stand-in is kept as the stand-in it played."""
    try:
        from . import music_bed as mb
        if not under and stop is None:
            return None
        rec = {"kind": plan.kind, "source": plan.source, "style": plan.style,
               "fallback": plan.fallback or "", "loop": True, "tone_kind": "",
               "for": kind or "reply"}
        if plan.kind == "stream" and under:
            alt = plan.fallback or mb.GENERATED_FOR.get(plan.style, "")
            rec.update(kind="under", source="generated", style=alt,
                       file=mb.file_for(alt).name)
        elif plan.kind == "stream":
            rec["tracks"] = [dict(t) for t in plan.tracks]
        elif plan.path is not None:
            rec["file"] = Path(plan.path).name
        if plan.credit:
            rec["credit"] = dict(plan.credit)
        return rec
    except Exception as e:  # noqa: BLE001 — a bed's record must never cost a reply
        log.warning("music-bed replay: recording: %s", e)
        return None


def _pick_now(kind: str):
    """The Pick submit would make now for a reading of `kind`, length
    aside (the row had a bed, so it was long): None when the music bed is
    off or the kind's own bed is switched off."""
    from . import music_bed as mb
    pick = mb.pick_for()
    if pick is None or kind in ("", "reply"):
        return pick
    if kind not in mb.kinds_on():
        return None
    return mb.Pick("generated", kind, kind)


def other_audio_now() -> Optional[bool]:
    """Whether anything else is playing now (music, the book, a player on
    this host), asked without pausing or ducking anything
    (`Coordinator.audio_playing`), within `music_bed.probe_wait_s`. None —
    which counts as playing — when it does not answer in time."""
    from . import music_bed as mb
    out: list = [None]

    def ask() -> None:
        try:
            from .route.coordinator import Coordinator
            out[0] = bool(Coordinator().audio_playing())
        except Exception as e:  # noqa: BLE001
            log.info("music-bed replay: probe: %s", e)
    t = threading.Thread(target=ask, name="bed-replay-probe", daemon=True)
    t.start()
    t.join(timeout=mb.probe_wait_s())
    return out[0]


def _same_plan(rec: dict, target):
    """The recorded bed as a Plan to play now, or None when it cannot be
    played as it was (its file pruned, Mopidy's tracks gone, a streaming bed
    on a target Mopidy does not reach)."""
    from . import music_bed as mb
    source, style = str(rec.get("source") or ""), str(rec.get("style") or "")
    credit = rec.get("credit") or None
    if rec.get("kind") == "stream":
        from . import music_bed_stream as st
        if st.available(mb._target_name(target)) is not None:
            return None
        have = {str(t.get("id")) for t in st.tracks()}
        tracks = tuple(t for t in rec.get("tracks") or ()
                       if isinstance(t, dict) and str(t.get("id")) in have)
        if not tracks:
            return None
        return mb.Plan("stream", source, style, tracks=tracks, credit=credit,
                       fallback=str(rec.get("fallback") or ""))
    if source == "generated":
        # Named for the level it was rendered at: the current level's file
        # of the same mood (music_bed.file_for), once rendered.
        p = mb.ready(style) if style else None
        return mb.Plan("under", "generated", style, path=p) if p else None
    name = str(rec.get("file") or "")
    if not name or "/" in name or name.startswith("."):
        return None
    # Favourites and library tracks are re-levelled in place, so the same
    # name is the current level.
    p = mb._dir() / name
    try:
        if not (p.is_file() and p.stat().st_size > 44):
            return None
    except OSError:
        return None
    return mb.Plan("under", source, style, path=p, credit=credit)


def _afresh(ex: dict, target, other_audio=None) -> Optional[dict]:
    """A bed chosen now for a row that recorded none: an agent's reply (it
    names its folder) by its length; anything else, nothing."""
    from . import music_bed as mb
    folder = ex.get("cwd")
    if not folder:
        return None
    try:
        from . import earcons
        tone = earcons.reply_name(folder)
    except Exception:  # noqa: BLE001
        tone = ""
    durs = [float(x) for x in (ex.get("clip_durations_s") or [])
            if isinstance(x, (int, float))]
    n = len(ex.get("clip_sentences") or ex.get("clip_uris") or [])
    pick = mb.wanted(sum(durs) if durs else None, n, "reply")
    if pick is None or not mb.ready_or_warm(pick, target):
        return {"tone": tone} if tone else None
    probe = other_audio or other_audio_now
    plan = mb.choose(pick, probe(), target)
    if plan is None:
        return {"tone": tone} if tone else None
    return {"plan": plan, "tone": tone}


def resolve(ex: dict, target, other_audio: Optional[Callable[[], Optional[bool]]] = None
            ) -> Optional[dict]:
    """What to play under a replay of the row whose extras are `ex` on
    `target`: ``{"plan": music_bed.Plan}``, ``{"tone": <earcon name>}``, or
    None (nothing, as before). `other_audio` asks whether anything else is
    playing (default `other_audio_now`); asked only when there is a bed to
    play. Never raises."""
    try:
        ex = ex or {}
        tone = str(ex.get("under_tone") or "")
        if ex.get("thinking"):
            # Never a bed under a thought; its `think` tone, as submit gives.
            return {"tone": "think"}
        rec = ex.get("bed")
        if not isinstance(rec, dict) and not tone and not ex.get("listener"):
            # Nothing recorded: a held reply never went through submit's bed
            # (its words waited for Play, David, 31 Oct 2026: "that one was
            # silent"), or a row from before beds were kept. Choose as submit
            # would now, by the row's own length, with the reply's tone
            # behind it.
            return _afresh(ex, target, other_audio)
        if not isinstance(rec, dict):
            return {"tone": tone} if tone else None
        pick = _pick_now(str(rec.get("for") or "reply"))
        if pick is None:
            return {"tone": tone} if tone else None
        plan = _same_plan(rec, target)
        if plan is None:
            # Pruned (or not playable here): chosen afresh, as submit
            # would now. Not ready: the background process is started.
            from . import music_bed as mb
            if not mb.ready_or_warm(pick, target):
                return {"tone": tone} if tone else None
            probe = other_audio or other_audio_now
            plan = mb.choose(pick, probe(), target)
        else:
            probe = other_audio or other_audio_now
            if probe() is not False:
                plan = None
        if plan is None:
            return {"tone": tone} if tone else None
        return {"plan": plan, "tone": tone}
    except Exception as e:  # noqa: BLE001 — a bed must never cost a replay
        log.warning("music-bed replay: %s", e)
        return None


def start(spec: Optional[dict], target, sink) -> tuple[bool, Optional[Callable]]:
    """Play `spec` (from `resolve`) under the replay about to start on
    `target`, as submit does: (looping under-player started, stop) as
    `music_bed.start` answers. A bed that fails as it starts gives way to
    its tone. Never raises."""
    if not spec or sink is None or target is None:
        return False, None
    try:
        plan = spec.get("plan")
        if plan is not None:
            from . import music_bed as mb
            under, stop = mb.start(plan, target, sink)
            if under or stop is not None:
                return under, stop
        tone = spec.get("tone")
        if tone:
            from . import earcons
            return bool(earcons.play_under(tone, target, sink, loop=True)), None
    except Exception as e:  # noqa: BLE001
        log.warning("music-bed replay: starting: %s", e)
    return False, None


def to_json(spec: Optional[dict]) -> Optional[dict]:
    """`spec` as JSON for the replay's follower, which starts it."""
    if not spec:
        return None
    out: dict = {}
    if spec.get("tone"):
        out["tone"] = spec["tone"]
    plan = spec.get("plan")
    if plan is not None:
        out["plan"] = {"kind": plan.kind, "source": plan.source, "style": plan.style,
                       "path": str(plan.path) if plan.path else None,
                       "tracks": [dict(t) for t in plan.tracks],
                       "credit": plan.credit, "fallback": plan.fallback}
    return out or None


def from_json(data) -> Optional[dict]:
    """The follower's copy of a spec (`to_json`)."""
    if not isinstance(data, dict):
        return None
    out: dict = {}
    if data.get("tone"):
        out["tone"] = str(data["tone"])
    p = data.get("plan")
    if isinstance(p, dict):
        from . import music_bed as mb
        out["plan"] = mb.Plan(str(p.get("kind") or "under"), str(p.get("source") or ""),
                              str(p.get("style") or ""),
                              path=Path(p["path"]) if p.get("path") else None,
                              tracks=tuple(t for t in p.get("tracks") or ()
                                           if isinstance(t, dict)),
                              credit=p.get("credit") or None,
                              fallback=str(p.get("fallback") or ""))
    return out or None
