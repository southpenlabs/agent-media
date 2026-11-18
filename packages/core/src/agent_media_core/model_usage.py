"""How much each model is used, per feature (server-contract §6.60; David,
17 Nov 2026: "it would be nice to have usage metrics on those models too").

Every model call a feature makes goes through `record` (intake/_summary.py's
`_chat`, which carries fast.py's chain, the summary, follow-ups and describe;
the canvas's gateway call in agent_media_visual/generate.py; photo_edit.run).
Counts only — never a prompt, an answer or any of their text:

  calls, failures (and by kind: timeout, refused, error), total ms (for an
  average), tokens in and out when the answer carried a `usage` block, and
  pictures for photo edits.

Kept per day (the server's local date), per role, per model, for 90 days, in
`<state>/model-usage.json`. Calls happen in more than one process (the
server, the detached playback child that speaks a summary), so each keeps
its own counts in memory and adds them to the file at most every few
seconds — read, add, prune, write whole under an flock — and once more when
it exits.

  {"v": 1, "days": {"2026-11-17": {"<role>": {"<model>": {"calls": 3, ...}}}}}
"""

from __future__ import annotations

import atexit
import json
import os
import re
import threading
import time
from pathlib import Path
from typing import Optional

from ._paths import state_dir
from agent_media_core import failures as _failures

FILE = "model-usage.json"
KEEP_DAYS = 90
FLUSH_S = 5.0
KINDS = ("timeout", "refused", "error")
FIELDS = ("calls", "failures", "timeout", "refused", "error", "ms", "tokens_in", "tokens_out", "images")

_lock = threading.Lock()
#: path → (day, role, model) → counts. Keyed by the file each count was made
#: for, so a count is never written into a state dir that changed since
#: (tests, which flush after their scratch home is gone).
_pending: dict[str, dict[tuple[str, str, str], dict]] = {}
_timer: Optional[threading.Timer] = None
_atexit = False


_ctx = threading.local()


class as_role:
    """`with model_usage.as_role("describe"): ...` — the calls made inside
    are counted under that role (a thread's own; mocks of `_chat` keep their
    signature)."""

    def __init__(self, role: str):
        self.role = role

    def __enter__(self):
        self.prev = getattr(_ctx, "role", None)
        _ctx.role = self.role
        return self

    def __exit__(self, *exc):
        _ctx.role = self.prev
        return False


def current_role() -> Optional[str]:
    return getattr(_ctx, "role", None)


# --- tags for the gateway ---------------------------------------------------------

#: Hosts that are a provider called directly, not a gateway (model_roles'
#: _HOST_PROVIDERS), and Ollama's port: these get no gateway tags.
DIRECT_HOSTS = ("api.openai.com", "api.venice.ai", "openrouter.ai", "api.groq.com",
                "generativelanguage.googleapis.com", "api.anthropic.com", "dashscope")
TAG_HEADER = "x-litellm-tags"
_TAG_SAFE = re.compile(r"[^A-Za-z0-9._:@/-]+")


def server_name() -> str:
    """This server's name as sasonica.com's list shows it (app.py's)."""
    import socket

    return os.environ.get("MEDIA_SERVER_NAME", "").strip() or socket.gethostname()


def is_gateway(base: str) -> bool:
    import urllib.parse

    try:
        u = urllib.parse.urlsplit(base if "//" in base else f"//{base}")
        host, port = (u.hostname or "").lower(), u.port
    except ValueError:
        return False
    return bool(host) and port != 11434 and not any(h in host for h in DIRECT_HOSTS)


def gateway_tags(role: str) -> list[str]:
    """`sasonica`, `feature:<role>`, `server:<name>`: what LiteLLM's spend
    tracking counts a call under. Never anything from the content."""
    return ["sasonica", f"feature:{_TAG_SAFE.sub('-', role or 'other')[:40]}",
            f"server:{_TAG_SAFE.sub('-', server_name())[:60]}"]


def gateway_headers(base: str, role: str) -> dict:
    """`{"x-litellm-tags": "sasonica,feature:tidy,server:red2"}` for a call
    to the gateway (LiteLLM 1.93 reads the header in
    proxy/litellm_pre_call_utils.py `add_request_tag_to_metadata`); `{}` for
    a provider called directly or Ollama, which get nothing new."""
    return {TAG_HEADER: ",".join(gateway_tags(role))} if is_gateway(base) else {}


def path() -> Path:
    return state_dir() / FILE


def _today(now: Optional[float] = None) -> str:
    return time.strftime("%Y-%m-%d", time.localtime(now if now is not None else time.time()))


def kind_of(why: str) -> str:
    """A failure's kind from the words a call path gives it."""
    w = (why or "").lower()
    if "did not answer within" in w or "timed out" in w or "timeout" in w:
        return "timeout"
    if " answered 4" in w or "refused" in w:
        return "refused"
    return "error"


def record(role: str, model: Optional[str], *, ok: bool, kind: Optional[str] = None,
           ms: float = 0.0, tokens_in: Optional[int] = None, tokens_out: Optional[int] = None,
           images: int = 0) -> None:
    """Count one call. Never raises, never blocks on the disk."""
    try:
        key = (_today(), str(role or "other")[:40], str(model or "default")[:120])
        where = str(path())
        with _lock:
            c = _pending.setdefault(where, {}).setdefault(key, dict.fromkeys(FIELDS, 0))
            c["calls"] += 1
            if not ok:
                c["failures"] += 1
                c[kind if kind in KINDS else "error"] += 1
            c["ms"] += max(0, int(ms))
            if isinstance(tokens_in, int) and tokens_in > 0:
                c["tokens_in"] += tokens_in
            if isinstance(tokens_out, int) and tokens_out > 0:
                c["tokens_out"] += tokens_out
            if images > 0:
                c["images"] += int(images)
        _schedule()
    except Exception as _exc:  # noqa: BLE001 — counting is never load-bearing
        _failures.swallowed(_exc)


def tokens(data: object) -> tuple[Optional[int], Optional[int]]:
    """`(in, out)` from an OpenAI-shaped answer's `usage`, or Nones."""
    u = data.get("usage") if isinstance(data, dict) else None
    if not isinstance(u, dict):
        return None, None

    def num(*names: str) -> Optional[int]:
        for n in names:
            v = u.get(n)
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                return int(v)
        return None

    return num("prompt_tokens", "input_tokens"), num("completion_tokens", "output_tokens")


def _schedule() -> None:
    global _timer, _atexit
    with _lock:
        if not _atexit:
            atexit.register(flush)
            _atexit = True
        if _timer is not None:
            return
        _timer = threading.Timer(FLUSH_S, _tick)
        _timer.daemon = True
        _timer.start()


def _tick() -> None:
    global _timer
    with _lock:
        _timer = None
    flush()


def _add(into: dict, day: str, role: str, model: str, c: dict) -> None:
    row = into.setdefault(day, {}).setdefault(role, {}).setdefault(model, {})
    for f in FIELDS:
        row[f] = int(row.get(f) or 0) + int(c.get(f) or 0)


def _prune(days: dict, now: Optional[float] = None) -> dict:
    cut = _today((now if now is not None else time.time()) - (KEEP_DAYS - 1) * 86400)
    return {d: v for d, v in days.items() if d >= cut}


def _load(p: Path) -> dict:
    try:
        data = json.loads(p.read_text())
    except (OSError, ValueError):
        return {}
    days = data.get("days") if isinstance(data, dict) else None
    return days if isinstance(days, dict) else {}


def flush() -> None:
    """Add this process's counts to the file(s). Never raises."""
    with _lock:
        if not _pending:
            return
        files = dict(_pending)
        _pending.clear()
    for where, taken in files.items():
        _flush_one(Path(where), taken)


def _flush_one(p: Path, taken: dict) -> None:
    try:
        from . import _lock as fcntl

        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p.with_name(FILE + ".lock"), "a") as lk:
            fcntl.flock(lk.fileno(), fcntl.LOCK_EX)
            days = _load(p)
            for (day, role, model), c in taken.items():
                _add(days, day, role, model, c)
            days = _prune(days)
            tmp = p.with_name(f".{FILE}.{os.getpid()}.tmp")
            tmp.write_text(json.dumps({"v": 1, "days": days}, sort_keys=True))
            os.replace(tmp, p)
    except Exception as _exc:  # noqa: BLE001 — put them back for the next try
        _failures.swallowed(_exc)
        with _lock:
            for (day, role, model), c in taken.items():
                cur = _pending.setdefault(str(p), {}).setdefault((day, role, model),
                                                                 dict.fromkeys(FIELDS, 0))
                for f in FIELDS:
                    cur[f] += c[f]


def days() -> dict:
    """Every day kept, this process's unwritten counts added in."""
    p = path()
    out = _load(p)
    out = {d: {r: {m: dict(c) for m, c in ms.items()} for r, ms in rs.items()}
           for d, rs in out.items() if isinstance(rs, dict)}
    with _lock:
        pend = dict(_pending.get(str(p)) or {})
    for (day, role, model), c in pend.items():
        _add(out, day, role, model, c)
    return _prune(out)


def _reset_for_tests() -> None:
    global _timer
    with _lock:
        _pending.clear()
        if _timer is not None:
            _timer.cancel()
        _timer = None
