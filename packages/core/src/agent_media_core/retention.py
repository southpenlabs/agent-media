"""How long the server keeps what it can let go of: the ages its pruning
jobs read, set from the app's Settings → Storage (Advanced) or here.

David, 14 Nov 2026, on the music log's pruning: "don't we prune other
things in agent-media?" — "should we include some other pruning features
with it?" — "Maybe only for advanced mode" — "y". So the ages that change
something a listener notices are in one place, each read by the job that
prunes it:

- ``clips``: rendered speech clips (audio_prune, the nightly `audio-prune`
  job). Past it an old reply no longer replays in its voice. Default
  ``MEDIA_AUDIO_KEEP_DAYS``, else 7; never forever (the cache would only
  grow).
- ``music_log``: the log of music around speech (speech_music, the nightly
  `state-gc`). Kept for ever unless an age is set (the proposal's
  decision: "everything maybe with a pruning option").
- ``history``: music and book play rows (StateStore.gc ``other_days``, the
  nightly `state-gc`). Kept for ever unless an age is set (David, 29 Sep
  2026: "the metadata should stay indefinitely"). Speech rows are never
  deleted whatever this says: they are the transcript.

A value is days, or None for "kept for ever" where that is allowed. The
file is ``<state_dir>/retention.json``; a key not in it is its default, so
an install that never opens the setting behaves as before.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional

from ._paths import state_dir

#: The longest age that can be set: ten years.
MAX_DAYS = 3650.0

#: Each age: the shortest it may be, whether "for ever" is allowed, and the
#: job that reads it (scheduler.JOBS). Defaults are default()'s.
KEEPS: "dict[str, dict]" = {
    "clips": {"min": 1.0, "forever": False, "job": "audio-prune"},
    "music_log": {"min": 1.0, "forever": True, "job": "state-gc"},
    "history": {"min": 7.0, "forever": True, "job": "state-gc"},
}

CLIPS_DEFAULT_DAYS = 7.0


def path() -> Path:
    return state_dir() / "retention.json"


def _read() -> dict:
    try:
        data = json.loads(path().read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _write(data: dict) -> None:
    p = path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, indent=1, sort_keys=True))
    tmp.replace(p)


def default(key: str) -> Optional[float]:
    """The age with nothing set: the clips' env or 7 days; the others for ever."""
    if key not in KEEPS:
        raise ValueError(f"unknown: {key}")
    if key == "clips":
        try:
            v = float(os.environ.get("MEDIA_AUDIO_KEEP_DAYS") or CLIPS_DEFAULT_DAYS)
        except ValueError:
            return CLIPS_DEFAULT_DAYS
        return v if v > 0 else CLIPS_DEFAULT_DAYS
    return None


def days(key: str) -> Optional[float]:
    """The age in force: the one set (None is for ever, where allowed), else
    the default. A stored value that no longer validates is ignored."""
    spec = KEEPS.get(key)
    if spec is None:
        raise ValueError(f"unknown: {key}")
    data = _read()
    if key not in data:
        return default(key)
    v = data[key]
    if v is None:
        return None if spec["forever"] else default(key)
    try:
        return _valid(key, v)
    except ValueError:
        return default(key)


def _valid(key: str, value: object) -> Optional[float]:
    spec = KEEPS[key]
    if value is None:
        if not spec["forever"]:
            raise ValueError(f"{key} cannot be kept for ever")
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("days must be a number, or null for ever")
    v = float(value)
    if not (spec["min"] <= v <= MAX_DAYS):
        raise ValueError(f"{key} is kept {spec['min']:g} to {MAX_DAYS:g} days")
    return v


def set_days(key: str, value: object) -> Optional[float]:
    """Set an age (days, or None for ever where allowed); ValueError for one
    out of range or of the wrong kind. "default" puts the default back."""
    if key not in KEEPS:
        raise ValueError(f"key must be one of {', '.join(KEEPS)}")
    data = _read()
    if value == "default":
        data.pop(key, None)
    else:
        data[key] = _valid(key, value)
    _write(data)
    return days(key)


def describe() -> "list[dict]":
    """Each age as the app shows it, with the job that prunes it and when
    that job last ran (scheduler.json)."""
    from . import scheduler

    out = []
    for key, spec in KEEPS.items():
        run = scheduler.last_run(spec["job"]) or {}
        out.append({
            "key": key,
            "days": days(key),
            "default": default(key),
            "set": key in _read(),
            "forever": spec["forever"],
            "min": spec["min"],
            "max": MAX_DAYS,
            "job": spec["job"],
            "last_run": {"at": run.get("at"), "ok": run.get("rc") == 0} if run else None,
        })
    return out


def jobs() -> "list[dict]":
    """Every scheduled job this host has, for the list under the ages: what
    it does, whether it runs here (and why not), and its last run."""
    from . import scheduler

    out = []
    for j in scheduler.jobs():
        run = scheduler.last_run(j.name) or {}
        out.append({
            "name": j.name,
            "what": j.what,
            "skip": scheduler.skip_reason(j) or None,
            "last_run": {"at": run.get("at"), "ok": run.get("rc") == 0,
                         "seconds": run.get("seconds")} if run else None,
        })
    return out
