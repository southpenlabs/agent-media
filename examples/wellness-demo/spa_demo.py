#!/usr/bin/env python3
"""Spa demo: three audio files for one made-up treatment day.

    python spa_demo.py [--scenario scenario.json] [--out ./out] [--minutes 5]

writes into --out:

- ``1-morning-rundown.mp3``: the therapist's day read to them (who, when,
  which room, the notes that matter) over a soft morning bed.
- ``2-treatment-room.mp3``: what the guest hears in the room. A welcome by
  name, a settling breath, the music with long silences, a bowl to start
  and to close, then the closing words.
- ``3-therapist-cues.mp3``: what the therapist hears in an earpiece during
  that treatment. Each part as it starts, a five-minute warning, time, and
  who is next.

A 55-minute treatment is compressed to --minutes (5 by default), keeping
the shape, so the demo can be played on a call. Pass --minutes 0 for real
time.

Nothing is played: the files are written and their paths printed, so
nothing can reach a speaker or phone. Rendering uses agent-media's
meditation renderer (agent_media_core.meditate), so the voice engine is
MEDIA_RENDER_ENGINE (edge if unset), or pass --engine/--voice. Every name
and booking in scenario.json is made up.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from contextlib import contextmanager
from pathlib import Path

from agent_media_core import meditate

HERE = Path(__file__).resolve().parent


def _spoken_minutes(m: int) -> str:
    return "one minute" if m == 1 else f"{m} minutes"


def rundown_script(sc: dict) -> str:
    bookings = sc["bookings"]
    paras = [f"Good morning, {sc['therapist']}. Here's your {sc['date']} at "
             f"{sc['venue']}: {len(bookings)} guests."]
    for b in bookings:
        paras.append(f"At {b['time']}, {b['guest']}, a {b['minutes']} minute "
                     f"{b['treatment']} in the {b['room']}. {b['notes'].capitalize()}.")
    paras.append("That's everything. Have a good day.")
    return "\n\n".join(paras)


def _pauses(seconds: float) -> str:
    """A long silence as pause markers, none over meditate.MAX_PAUSE_S."""
    out = []
    left = max(1.0, seconds)
    while left > 0:
        step = min(left, meditate.MAX_PAUSE_S)
        out.append(f"[[pause: {step:.0f}]]")
        left -= step
    return "\n\n".join(out)


def room_script(t: dict, scale: float) -> str:
    total = sum(p["minutes"] for p in t["phases"]) * 60 * scale
    return "\n\n".join([
        t["welcome"], "[[pause: 4]]", t["settle"], "[[pause: 6]]", "[[bell]]",
        _pauses(total), "[[bell]]", t["closing"],
    ])


def cues_script(sc: dict, scale: float) -> str:
    t = sc["treatment"]
    first = next((b for b in sc["bookings"] if b["guest"] == t["guest"]), {})
    nxt = next((b for i, b in enumerate(sc["bookings"])
                if i and sc["bookings"][i - 1] is first), None)
    phases = t["phases"]
    paras = [f"{t['guest']}, {t['name']}, {t['room']}. "
             f"{first.get('notes', '').capitalize()}."]
    done = 0
    for i, p in enumerate(phases):
        lead = ("Starting with" if i == 0
                else f"{_spoken_minutes(done).capitalize()} in. Next:")
        paras.append(f"{lead} {p['name']}, {_spoken_minutes(p['minutes'])}.")
        secs = p["minutes"] * 60 * scale
        if i == len(phases) - 1 and p["minutes"] > 5:
            paras.append(_pauses(secs - 5 * 60 * scale))
            paras.append("Five minutes to go.")
            paras.append(_pauses(5 * 60 * scale))
        else:
            paras.append(_pauses(secs))
        done += p["minutes"]
    end = "Time. Their closing words are playing now."
    if nxt:
        end += f" Your next guest is {nxt['guest']} at {nxt['time']}, in the {nxt['room']}."
    paras.append(end)
    return "\n\n".join(paras)


@contextmanager
def _env(**values: str):
    old = {k: os.environ.get(k) for k in values}
    os.environ.update(values)
    try:
        yield
    finally:
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def build(sc: dict, out: Path, minutes: float, engine=None, voice=None,
          renderer=None) -> list[Path]:
    t = sc["treatment"]
    real = sum(p["minutes"] for p in t["phases"])
    scale = 1.0 if minutes <= 0 else min(1.0, minutes / real)
    jobs = [
        ("1-morning-rundown.mp3", rundown_script(sc),
         dict(MEDIA_MEDITATE_RATE="+0%", MEDIA_MEDITATE_TAIL_S="8",
              MEDIA_MEDITATE_GAP_S="1.2", MEDIA_MEDITATE_BED_PEAK_DBFS="-26"), "morning"),
        ("2-treatment-room.mp3", room_script(t, scale),
         dict(MEDIA_MEDITATE_TAIL_S="20"), t.get("bed") or "nature"),
        ("3-therapist-cues.mp3", cues_script(sc, scale),
         dict(MEDIA_MEDITATE_RATE="+0%", MEDIA_MEDITATE_TAIL_S="5",
              MEDIA_MEDITATE_GAP_S="0.5"), "none"),
    ]
    out.mkdir(parents=True, exist_ok=True)
    written = []
    for name, script, env, bed in jobs:
        with _env(**env):
            rendered = meditate.render(script, engine=engine, voice=voice,
                                       bed=bed, renderer=renderer)
        if rendered is None:
            raise SystemExit(f"could not render {name} (is the voice engine reachable?)")
        dest = out / name
        shutil.copyfile(rendered, dest)
        written.append(dest)
    return written


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--scenario", type=Path, default=HERE / "scenario.json")
    ap.add_argument("--out", type=Path, default=Path("wellness-demo-out"))
    ap.add_argument("--minutes", type=float, default=5.0,
                    help="compress the treatment to this many minutes (0: real time)")
    ap.add_argument("--engine")
    ap.add_argument("--voice")
    ap.add_argument("--print-scripts", action="store_true",
                    help="print the three scripts and render nothing")
    a = ap.parse_args(argv)
    sc = json.loads(a.scenario.read_text())
    if a.print_scripts:
        real = sum(p["minutes"] for p in sc["treatment"]["phases"])
        scale = 1.0 if a.minutes <= 0 else min(1.0, a.minutes / real)
        for title, s in (("morning rundown", rundown_script(sc)),
                         ("treatment room", room_script(sc["treatment"], scale)),
                         ("therapist cues", cues_script(sc, scale))):
            print(f"--- {title} ---\n{s}\n")
        return 0
    for p in build(sc, a.out, a.minutes, a.engine, a.voice):
        print(p.resolve())
    return 0


if __name__ == "__main__":
    sys.exit(main())
