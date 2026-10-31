"""Mixing the music under the voice like a radio DJ (roadmap 39; David,
31 Oct 2026: "DJ mixing").

A music bed (music_bed.py) used to sit at one level under a reading, fade in
as it started and fade out after it. Now, each a switch in Settings → Music
(GET/POST /speech/music-bed `settings`, kind "bool", all on by default):

- **breathe** — the music sits at the chosen level under the words and
  swells MEDIA_MUSIC_BED_BREATHE_DB (6 dB) above it in the pauses (and
  before the first word, and after the last). The level is the one under
  the words, not in the pauses: with the dip taken below the chosen level a
  quiet bed went under a phone's hearing altogether (David, 31 Oct 2026, no
  music heard; the player ran the whole reply). Sent as `gain_db` raised by
  the swell, which the ride takes down again under the words: down in
  MEDIA_MUSIC_BED_ATTACK_MS
  (80), up in MEDIA_MUSIC_BED_RELEASE_MS (400), and only once the voice has
  been quiet for MEDIA_MUSIC_BED_HOLD_MS (250), so the gaps between words
  never pump.
- **carve** — while the voice sounds, a cut in the voice band of the music
  (MEDIA_MUSIC_BED_CARVE_DB, 3 dB — a phone's speaker plays little but that
  band, so a deeper cut is the music gone; from MEDIA_MUSIC_BED_CARVE_LO_HZ 300 to
  MEDIA_MUSIC_BED_CARVE_HI_HZ 3000: a peaking EQ at their geometric mean,
  log2(hi/lo) octaves wide), following the same envelope as breathe.
- **intro** — the music starts MEDIA_MUSIC_BED_INTRO_MS (1500) before the
  first word: this host waits that long after starting its under player
  (`intro_wait`); a phone holds the first sentence itself. Never without a
  bed.
- **outro** — at the natural end the music finishes rather than just fading:
  a generated bed plays its ending (music_bed_outro: its tonic chord rung
  out, about 3.5 s) once, while the loop fades under it in
  MEDIA_MUSIC_BED_OUTRO_MS (800); a track (library, favourites) fades with
  the sweep closing.
- **sweep** — the fades go through a low-pass filter: from
  MEDIA_MUSIC_BED_SWEEP_HZ (400) to fully open over the fade in, and closing
  the same way over the fade out.
- **carry** — when another reading is about to start as one ends (a reply
  waiting for its turn or still being made: the speech queue), the bed is
  held rather than ended, for up to MEDIA_MUSIC_BED_CARRY_S (20). The next
  reading with the same bed carries it on; a different one crossfades over
  MEDIA_MUSIC_BED_XFADE_MS (3000); nothing within the window, the outro. A
  thinking readout keeps its harp, and a bed held across it carries on
  MEDIA_MUSIC_BED_THOUGHT_DB (8) lower underneath until the reply after it.

Each switch's default is MEDIA_MUSIC_BED_<NAME> (on unless 0/off/false/no);
Settings' choice wins.

Where: a phone is told the whole mix as one stored property beside `under`
(sinks/speech.py UNDER_MIX_PROPERTY, `params` below) and does all of it in
the app (its voice player's level, a Media3 AudioProcessor on the side
player); the hold as UNDER_HOLD_PROPERTY. This host's own under player (a
second mpv) is ridden through its IPC socket (under_mix.py). The streaming
source (Mopidy) keeps its plain fade.

The music level (`gain_db`, a "range" setting) is applied live too: a file
rendered or normalised at another level plays at the chosen one by the
difference (`level_offset`) until the renderer catches up.
"""

from __future__ import annotations

import logging
import os
import re
import time
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

#: (id, label, hint) of each switch, in the order Settings lists them.
SWITCHES = (
    ("breathe", "Breathe with the voice",
     "The music dips under the words and comes back up a little in the pauses."),
    ("carve", "Make room for the voice",
     "While the voice speaks, the music's voice band is turned down, not only its level."),
    ("intro", "Lead in before the voice",
     "The music starts a moment before the first word."),
    ("outro", "Finish with an ending",
     "Generated music ends on a closing chord; a track fades out through a filter."),
    ("sweep", "Filter sweeps on fades",
     "Fades in and out open and close a low-pass filter, like a DJ's."),
    ("carry", "Keep the music going between replies",
     "When another reply is about to start, the music carries on into it "
     "(a different piece crossfades)."),
    ("vocal", "Sing it out after long replies",
     "After a long reply over one of your favourites, the song itself comes in, "
     "vocals and all, and plays to the end of its next chorus."),
)
IDS = tuple(s[0] for s in SWITCHES)

DEFAULTS = {
    "BREATHE_DB": 6.0, "ATTACK_MS": 80.0, "RELEASE_MS": 400.0, "HOLD_MS": 250.0,
    "CARVE_DB": 3.0, "CARVE_LO_HZ": 300.0, "CARVE_HI_HZ": 3000.0,
    "INTRO_MS": 1500.0, "SWEEP_HZ": 400.0, "OUTRO_MS": 800.0,
    "CARRY_S": 20.0, "XFADE_MS": 3000.0, "THOUGHT_DB": 8.0,
}


def tunable(name: str) -> float:
    """MEDIA_MUSIC_BED_<name>, else its default; never negative."""
    d = DEFAULTS[name]
    try:
        v = float(os.environ.get(f"MEDIA_MUSIC_BED_{name}", "") or d)
    except ValueError:
        v = d
    return max(0.0, v)


def stored(name: str) -> Optional[bool]:
    from . import music_bed
    v = music_bed._stored().get(name)
    return v if isinstance(v, bool) else None


def env_on(name: str) -> bool:
    return (os.environ.get(f"MEDIA_MUSIC_BED_{name.upper()}") or "").strip().lower() not in (
        "0", "off", "false", "no")


def on(name: str) -> bool:
    """Whether switch `name` is on: Settings', else its env, else on."""
    s = stored(name)
    return env_on(name) if s is None else s


def set_switch(name: str, value: Optional[bool]) -> None:
    from . import music_bed
    if name not in IDS:
        raise ValueError(f"no music bed setting {name!r}")
    d = music_bed._stored()
    if value is None:
        d.pop(name, None)
    else:
        d[name] = bool(value)
    music_bed._store(d)


def settings() -> list[dict]:
    """The switches as GET /speech/music-bed lists them (kind "bool")."""
    return [{"id": i, "kind": "bool", "label": label, "hint": hint,
             "value": on(i), "set": stored(i) is not None}
            for i, label, hint in SWITCHES]


# ---- the level, applied live ---------------------------------------------------------------

_GAIN_TAG = re.compile(r"-v\d+-g(-?[0-9.]+)\.wav$")


def file_gain(path) -> Optional[float]:
    """The music level a generated bed's file was rendered at (its `-g<dB>`
    tag; none is the default), or None for a file that is not one."""
    from . import music_bed
    name = Path(path).name
    if not (name.startswith("music-bed-") and name.endswith(".wav")):
        return None
    m = _GAIN_TAG.search(name)
    if m:
        try:
            return float(m.group(1))
        except ValueError:
            return None
    return music_bed.DEFAULT_GAIN_DB if re.search(r"-v\d+\.wav$", name) else None


def level_offset(source: str, path) -> float:
    """How far (dB) the file under a reading is from the chosen level: a
    generated bed rendered at another gain, a kept track normalised to
    another target. 0 when caught up or not known."""
    try:
        if path is None:
            return 0.0
        from . import music_bed
        if source == "generated":
            g = file_gain(path)
            return 0.0 if g is None else round(music_bed.gain_db() - g, 2)
        if source in ("library", "favourites"):
            from . import music_bed_library as lib
            mod = lib
            if source == "favourites":
                from . import music_bed_favourites as mod  # noqa: F811
            name = Path(path).name
            for e in mod.tracks(None, mod.load()):
                if e.get("file") == name:
                    have = float(e.get("lufs_target", lib.DEFAULT_LUFS))
                    return round(lib.target_lufs() - have, 2)
    except Exception as e:  # noqa: BLE001
        log.debug("music-bed mix: level offset: %s", e)
    return 0.0


# ---- what a player is told --------------------------------------------------------------

def _stored_level() -> float:
    from . import music_bed
    try:
        return round(float(music_bed.gain_db()), 2)
    except Exception:  # noqa: BLE001
        return 0.0


def params(source: str, style: str, path, long: bool = False) -> dict:
    """The mix for one bed, as a phone is told it (JSON) and this host's
    under player uses it: every effect's amount, 0 for one switched off.
    `outro` is the ending's file (a path; the sink makes it a clip URI for
    a phone), "" for none. `long` (a long reply, music_bed_vocal.is_long)
    over a favourite whose original is kept: `after`, the vocal handover,
    which replaces the ending and the carry."""
    breathe = on("breathe")
    carve = on("carve")
    outro = ""
    if on("outro") and source == "generated":
        try:
            from . import music_bed_outro
            p = music_bed_outro.ready(style)
            outro = str(p) if p is not None else ""
        except Exception as e:  # noqa: BLE001
            log.debug("music-bed mix: outro: %s", e)
    after = None
    if long and source == "favourites" and on("vocal"):
        try:
            from . import music_bed_vocal
            after = music_bed_vocal.after_for(path)
        except Exception as e:  # noqa: BLE001
            log.debug("music-bed mix: vocal: %s", e)
    swell = tunable("BREATHE_DB") if breathe else 0.0
    out = {
        "v": 1,
        # The chosen level is under the words; the pauses swell above it (a
        # player's ride takes `breathe_db` off this while the voice sounds).
        "gain_db": round(level_offset(source, path) + swell, 2),
        "breathe_db": swell,
        # The stored level this mix was made at: a later change moves a bed
        # already playing by the difference (UNDER_LEVEL_PROPERTY, Rider).
        "level_db": _stored_level(),
        "attack_ms": tunable("ATTACK_MS"),
        "release_ms": tunable("RELEASE_MS"),
        "hold_ms": tunable("HOLD_MS"),
        "carve_db": tunable("CARVE_DB") if carve else 0.0,
        "carve_lo_hz": tunable("CARVE_LO_HZ"),
        "carve_hi_hz": tunable("CARVE_HI_HZ"),
        "intro_ms": tunable("INTRO_MS") if on("intro") else 0.0,
        "sweep_hz": tunable("SWEEP_HZ") if on("sweep") else 0.0,
        "outro": outro,
        "outro_ms": tunable("OUTRO_MS"),
        "carry_ms": tunable("CARRY_S") * 1000.0 if on("carry") else 0.0,
        "xfade_ms": tunable("XFADE_MS"),
        "thought_db": tunable("THOUGHT_DB"),
    }
    if after:
        out.update(after=after, outro="", carry_ms=0.0)
    return out


def for_plan(plan, long: bool = False) -> Optional[dict]:
    """`params` for a music_bed.Plan played as an under clip; None for a
    streaming one (Mopidy's fade only) or on any failure."""
    try:
        if plan is None or plan.kind != "under" or plan.path is None:
            return None
        return params(plan.source, plan.style, plan.path, long)
    except Exception as e:  # noqa: BLE001 — a mix must never cost a bed
        log.warning("music-bed mix: %s", e)
        return None


# ---- what comes next ------------------------------------------------------------------------

def next_coming() -> bool:
    """Whether another reading is about to start: a live entry in the speech
    queue's registry, waiting for its turn or still being made (and not
    wedged past MEDIA_SPEECH_PENDING_TTL_S). The reading now playing took
    its own entry out when it took the token. Never raises."""
    try:
        from .intake import submit
        floor = time.time() - submit._pending_ttl_s()
        for f in submit._speech_wait_dir().iterdir():
            try:
                pid = int(f.name.split(".", 1)[0])
            except (ValueError, IndexError):
                continue
            if not submit._pid_alive(pid):
                continue
            try:
                lines = f.read_text().splitlines()
                seq = float(lines[1].strip()) if len(lines) > 1 else time.time()
            except (OSError, ValueError, IndexError):
                continue
            if len(lines) > 3 and lines[3].strip() == "1" and seq < floor:
                continue
            return True
    except Exception as e:  # noqa: BLE001
        log.debug("music-bed mix: queue: %s", e)
    return False


def at_end(sink, target, *, bed: bool, thought: bool, cut: bool) -> bool:
    """A reading is over (submit's cleanup, before stop_under). With carry
    on, the reading ended by itself, and another reading about to start:
    hold the bed for it (`SinkSpeech.hold_under`: this host keeps its under
    player going and out of stop_under's reach; a phone is told the window).
    Otherwise a bed this host held across the reading (a thought between
    replies) is let go to its outro. True when held. Never raises."""
    try:
        hold = getattr(sink, "hold_under", None)
        if hold is None:
            return False
        if not cut and (bed or thought) and on("carry") and next_coming():
            return bool(hold(target, int(tunable("CARRY_S") * 1000)))
        release = getattr(sink, "release_held", None)
        if release is not None:
            release(target, quick=cut)
    except Exception as e:  # noqa: BLE001 — never cost the cleanup
        log.debug("music-bed mix: at end: %s", e)
    return False


def intro_wait(sink, target, sleep=time.sleep) -> float:
    """After a bed started on this host's own under player, wait out the
    intro before the first word (a phone holds its first sentence itself,
    and a bed carried on from the reading before needs none). The seconds
    waited. Never raises."""
    try:
        led = getattr(sink, "under_lead_ms", None)
        ms = led(target) if led is not None else 0
        if ms and ms > 0:
            sleep(ms / 1000.0)
            return ms / 1000.0
    except Exception as e:  # noqa: BLE001
        log.debug("music-bed mix: intro: %s", e)
    return 0.0
