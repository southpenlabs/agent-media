"""A reply's trace from the moment it is committed, before it has a row.

A spoken reply's history row is written once, when its playback ends
(`submit._archive`): a 56-second reply has no row for 56 seconds. Two
checks read only history and so missed one still being spoken (David,
3 Nov 2026: a reply read twice, the second a held copy he was then played
again on opening the thread):

- the Stop hook's own text dedup (`hook_claude_code._dedup_seen`), and
- sessiond's safety net (`Supervisor._recover_reply`), which re-runs the
  Stop hook for a chat that exited soon after its turn when the history
  has nothing from it 45 s later.

So the Stop path claims the reply here, by its dedup key, the moment it
commits to it (`hook_claude_code._play_now`): one small file per reply in
`<state>/claude-stamps/reply-claims/`, created with `O_EXCL` so two hooks
racing for the same reply cannot both win. It names the session, the time
and the process that will speak it, and is marked `done` once that process
has handed the reply on (spoken, held or filed).

A claim counts while its process is still alive (up to `WORK_S`), and
once done for `TTL_S` more. A process killed before it finished — the case
sessiond's net exists for, a Stop hook taken down with its chat — leaves a
claim that no longer counts, so the reply is recovered as before. Never
raises.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Optional

from .._paths import state_dir

#: How long a done claim is believed: the hook's own dedup window.
TTL_S = 300.0
#: How long a claim still being worked is believed while its process
#: lives: a reply can wait its turn behind others, then speak for minutes.
WORK_S = 3600.0


def key_of(text: str) -> str:
    """The reply's dedup key, as the Stop hook makes it."""
    return hashlib.sha1((text or "").encode("utf-8")).hexdigest()


def _dir() -> Optional[Path]:
    try:
        d = state_dir() / "claude-stamps" / "reply-claims"
        d.mkdir(parents=True, exist_ok=True)
        return d
    except OSError:
        return None


def _alive(pid) -> bool:
    try:
        from .. import procinfo
        return procinfo.alive(int(pid))
    except Exception:  # noqa: BLE001 — unsure: believe the claim
        return True


def _read(path: Path) -> Optional[dict]:
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _counts(rec: Optional[dict], now: float) -> bool:
    """A claim still stands: fresh, and handed on or still being worked."""
    if not rec:
        return False
    try:
        at = float(rec.get("at") or 0)
    except (TypeError, ValueError):
        return False
    if rec.get("done"):
        try:
            at = float(rec.get("done_at") or at)
        except (TypeError, ValueError):
            pass
        return now - at <= TTL_S
    return now - at <= WORK_S and _alive(rec.get("pid"))


def claim(key: str, session: str = "") -> bool:
    """Claim reply `key` for this process. True when it is ours to speak;
    False when a claim that still stands has it. A stale one (old, or its
    process gone before it was done) is taken over. Any filesystem trouble
    answers True: a reply is never dropped for want of a marker."""
    d = _dir()
    if d is None or not key:
        return True
    path = d / key
    rec = {"at": round(time.time(), 3), "pid": os.getpid(), "session": session or "",
           "done": False}
    body = json.dumps(rec).encode()
    try:
        fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
    except FileExistsError:
        if _counts(_read(path), time.time()):
            return False
        try:
            # Stale: taken over. Replace, not rewrite in place, so a reader
            # never sees half a record.
            tmp = path.with_name(f".{key}.{os.getpid()}")
            tmp.write_bytes(body)
            os.replace(tmp, path)
        except OSError:
            pass
        _prune(d)
        return True
    except OSError:
        return True
    try:
        os.write(fd, body)
    finally:
        os.close(fd)
    _prune(d)
    return True


def done(key: str) -> None:
    """The reply `key` has been handed on (spoken, held or filed): its claim
    stands for its TTL whatever happens to this process now."""
    _update(key, done=True, done_at=round(time.time(), 3))


def release(key: str) -> None:
    """Give the claim back (the reply was not handed on after all): a retry,
    or sessiond's recovery, may take it."""
    d = _dir()
    if d is None or not key:
        return
    rec = _read(d / key)
    if rec and int(rec.get("pid") or 0) == os.getpid():
        try:
            (d / key).unlink()
        except OSError:
            pass


def _update(key: str, **fields) -> None:
    d = _dir()
    if d is None or not key:
        return
    path = d / key
    rec = _read(path)
    if not rec or int(rec.get("pid") or 0) != os.getpid():
        return
    rec.update(fields)
    try:
        tmp = path.with_name(f".{key}.{os.getpid()}")
        tmp.write_text(json.dumps(rec))
        os.replace(tmp, path)
    except OSError:
        pass


def standing(key: str) -> bool:
    """Whether reply `key` has a claim that still stands."""
    d = _dir()
    if d is None or not key:
        return False
    return _counts(_read(d / key), time.time())


def for_session(session: str, since: float) -> dict:
    """The claims of `session` made at or after `since` that still stand:
    `{"done": n, "working": n}`."""
    out = {"done": 0, "working": 0}
    d = _dir()
    if d is None or not session:
        return out
    now = time.time()
    try:
        paths = list(d.iterdir())
    except OSError:
        return out
    for p in paths:
        if p.name.startswith("."):
            continue
        rec = _read(p)
        if not rec or rec.get("session") != session:
            continue
        try:
            if float(rec.get("at") or 0) < since:
                continue
        except (TypeError, ValueError):
            continue
        if not _counts(rec, now):
            continue
        out["done" if rec.get("done") else "working"] += 1
    return out


def _prune(d: Path) -> None:
    """Claims older than any that could still count go; cheap, at most a
    few dozen."""
    cutoff = time.time() - WORK_S - TTL_S
    try:
        for p in d.iterdir():
            try:
                if p.stat().st_mtime < cutoff:
                    p.unlink()
            except OSError:
                continue
    except OSError:
        pass
