"""Calm mode: replies said slower and softer over a beatless bed that drifts
out (docs/proposals/2026-10-31-relax-and-meditate.md §2; David, 1 Nov 2026:
"yeah, let's do it").

Two switches in Settings → Music (music_bed.settings, stored with the bed's
own settings): **Calm mode** (on now) and **Calm in the evenings** (on from
MEDIA_CALM_FROM, 21:00, until MEDIA_CALM_UNTIL, 06:00, local time). Unset,
MEDIA_CALM (off) decides the first. While calm:

- **The voice** — a reply is rendered at MEDIA_CALM_RATE (-10%) and
  MEDIA_CALM_VOLUME (-15%), as a thinking readout is said slower and
  quieter (intake/submit.py; the device's own voice gets the same).
- **The bed** — whatever the choice (unless it is off), a reply's bed is the
  generated MEDIA_CALM_MOOD (night): no beat, never a favourite
  (music_bed.pick_for). The kind beds (people, question, alert) keep theirs.
- **Drift it out** — no song sings out after a long reply, and the bed's
  loop fades under its ending over MEDIA_CALM_DRIFT_S (60) rather than
  MEDIA_MUSIC_BED_OUTRO_MS (music_bed_mix.params).
"""

from __future__ import annotations

import os
import re
from datetime import datetime
from typing import Optional

LABEL = "Calm mode"
HINT = ("Replies in a slower, softer voice over quiet music with no beat, which "
        "drifts out after the words. No song sings out.")
EVENING_LABEL = "Calm in the evenings"
EVENING_HINT = "Calm mode by itself in the evening and overnight."


def _store():
    from . import music_bed
    return music_bed


def stored(key: str) -> Optional[bool]:
    v = _store()._stored().get(key)
    return v if isinstance(v, bool) else None


def set_stored(key: str, value: Optional[bool]) -> None:
    if value is not None and not isinstance(value, bool):
        raise ValueError(f"{key} must be true, false or null, not {value!r}")
    mb = _store()
    d = mb._stored()
    if value is None:
        d.pop(key, None)
    else:
        d[key] = value
    mb._store(d)


def on() -> bool:
    """The Calm mode switch (MEDIA_CALM when Settings has not chosen)."""
    s = stored("calm")
    if s is not None:
        return s
    return (os.environ.get("MEDIA_CALM") or "").strip().lower() in ("1", "on", "true", "yes")


def evening_on() -> bool:
    s = stored("calm_evening")
    return bool(s)


def _minutes(raw: str, default: str) -> int:
    m = re.fullmatch(r"\s*(\d{1,2}):(\d{2})\s*", raw or "") or re.fullmatch(r"(\d{1,2}):(\d{2})", default)
    return int(m.group(1)) % 24 * 60 + int(m.group(2)) % 60


def in_evening(when: Optional[datetime] = None) -> bool:
    """Whether `when` (now) falls between MEDIA_CALM_FROM and MEDIA_CALM_UNTIL,
    a window that may run past midnight."""
    when = when or datetime.now()
    start = _minutes(os.environ.get("MEDIA_CALM_FROM", ""), "21:00")
    end = _minutes(os.environ.get("MEDIA_CALM_UNTIL", ""), "06:00")
    now = when.hour * 60 + when.minute
    if start == end:
        return False
    return start <= now < end if start < end else (now >= start or now < end)


def active(when: Optional[datetime] = None) -> bool:
    """Calm now: switched on, or the evening switch and the evening. Never raises."""
    try:
        return on() or (evening_on() and in_evening(when))
    except Exception:  # noqa: BLE001 — calm is a nicety, never a reply's cost
        return False


def _pct(name: str, default: str) -> str:
    raw = (os.environ.get(name) or default).strip()
    return raw if raw.endswith("%") else f"{raw}%"


def prosody() -> tuple[str, str]:
    """(rate, volume) a calm reply is said with."""
    return _pct("MEDIA_CALM_RATE", "-10%"), _pct("MEDIA_CALM_VOLUME", "-15%")


def mood() -> str:
    from . import music_bed
    m = (os.environ.get("MEDIA_CALM_MOOD") or "night").strip()
    return m if m in music_bed.GENERATED else "night"


def drift_ms() -> float:
    try:
        return max(5.0, min(300.0, float(os.environ.get("MEDIA_CALM_DRIFT_S") or 60))) * 1000.0
    except ValueError:
        return 60000.0


def settings() -> list[dict]:
    return [{"id": "calm", "kind": "bool", "label": LABEL, "hint": HINT,
             "value": on(), "set": stored("calm") is not None},
            {"id": "calm_evening", "kind": "bool", "label": EVENING_LABEL, "hint": EVENING_HINT,
             "value": evening_on(), "set": stored("calm_evening") is not None}]
