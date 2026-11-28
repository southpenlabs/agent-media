"""Small records of things that went wrong quietly, for `media doctor`.

David, 28 Nov 2026 ("Build all four"): a reply picked up after a restart
that never played, and a replay the phone never confirmed hearing, were each
seen only by someone listening. Each is noted here as it happens, from
whichever process sees it, and `media doctor` reads them back.

`<state>/health-log.json`: {kind: [{"at": epoch, ...detail}, ...]}, the last
KEEP of each kind. Best effort: a note that cannot be written is dropped.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Optional

from ._paths import state_dir

KEEP = 50


def path() -> Path:
    return state_dir() / "health-log.json"


def _load() -> dict:
    try:
        got = json.loads(path().read_text())
    except (OSError, ValueError):
        return {}
    return got if isinstance(got, dict) else {}


def note(kind: str, now: Optional[float] = None, **detail) -> None:
    try:
        data = _load()
        rows = data.get(kind) if isinstance(data.get(kind), list) else []
        rows = (rows + [{"at": round(time.time() if now is None else now, 1), **detail}])[-KEEP:]
        data[kind] = rows
        p = path()
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(f".{os.getpid()}.tmp")
        tmp.write_text(json.dumps(data, separators=(",", ":")))
        os.replace(tmp, p)
    except OSError:
        pass


def recent(kind: str, within_s: float, now: Optional[float] = None) -> list[dict]:
    t = time.time() if now is None else now
    rows = _load().get(kind)
    return [r for r in rows if isinstance(r, dict) and t - float(r.get("at") or 0) <= within_s] \
        if isinstance(rows, list) else []
