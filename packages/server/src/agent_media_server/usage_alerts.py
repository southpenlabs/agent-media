"""A phone note before a plan or credit runs out (David, 25 Nov 2026: "do all",
of "a phone alert before something runs out").

The scheduler's `usage-alerts` job (`media usage check`, every 30 minutes)
posts `POST /usage/alerts/check` with the host's token; this looks at every
plan and credit the Coding agents page shows (plan_usage.py, credit_usage.py)
and posts one note for each that is close:

- a window of a plan at MEDIA_USAGE_ALERT_AT% (90 by default) or more — the
  Claude plan's 5 hours, week, and a model's own week (Fable's); OpenCode Go's,
  ChatGPT's (Codex) and Antigravity's;
- credit that is low (under MEDIA_CREDIT_LOW), used up, or a key refused.

Once per window: a window is told again only after it has reset (its
`resets_at` moves), credit only after it has been fine again since. Kept in
`usage-alerts.json`. The note is the phone's own (notes.py), `usage-<key>`,
gone after 12 hours; the in-app bars stay where they are.
"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Callable, Optional

from agent_media_core import failures as _failures

_LOCK = threading.Lock()
Post = Callable[[dict], object]


def alert_at() -> int:
    try:
        return int(os.environ.get("MEDIA_USAGE_ALERT_AT") or 90)
    except ValueError:
        return 90


def _path() -> Path:
    from agent_media_core._paths import state_dir

    return state_dir() / "usage-alerts.json"


def _load() -> dict:
    try:
        data = json.loads(_path().read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _save(data: dict) -> None:
    p = _path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=1))
    os.replace(tmp, p)


_KIND = {"session": "5-hour limit", "weekly_all": "weekly limit", "monthly": "monthly limit"}


def _window_name(plan: str, w: dict) -> str:
    kind = w.get("kind")
    if kind == "weekly_scoped":
        return f"{w.get('label') or 'a model'}'s own weekly limit"
    if kind == "model":
        return f"{plan}'s {w.get('label') or 'model'} quota"
    return f"{plan}'s {_KIND.get(kind, 'limit')}"


def _when(at: Optional[int]) -> str:
    return time.strftime("%a %-I:%M %p", time.localtime(at)) if at else ""


def gather() -> list[dict]:
    """What is close now: `[{key, mark, title, text}]`. `mark` changes when
    the condition starts over (a window's reset), so it is told once each time."""
    from . import credit_usage, plan_usage

    out: list[dict] = []
    at = alert_at()
    plans: list[tuple[str, str, dict]] = []
    try:
        ok, claude = plan_usage.claude_now()
        if ok:
            plans.append(("claude", "Claude", claude))
    except Exception as e:
        _failures.swallowed(e)
    for pid, p in plan_usage.PLANS.items():
        try:
            if not p["here"]():
                continue
            ok, got = plan_usage.plan_now(pid)
        except Exception as e:
            _failures.swallowed(e)
            continue
        if ok:
            plans.append((pid, p["name"], got))
    for pid, name, got in plans:
        for w in got.get("windows") or []:
            pct = int(w.get("percent") or 0)
            if pct < at:
                continue
            what = _window_name(name, w)
            when = _when(w.get("resets_at"))
            out.append({
                "key": f"{pid}-{w.get('kind')}-{w.get('label') or ''}".lower().replace(" ", "-"),
                "mark": str(w.get("resets_at") or ""),
                "title": f"{what[0].upper()}{what[1:]} at {pct}%",
                "text": ("Used up" if pct >= 100 else f"{100 - pct}% left")
                        + (f", resets {when}." if when else "."),
            })
    try:
        credits = credit_usage.found()
    except Exception as e:
        _failures.swallowed(e)
        credits = []
    for r in credits:
        if r.get("state") not in ("low", "empty", "rejected"):
            continue
        if r["state"] == "rejected":
            title, text = f"{r['name']} refused its key", "Check it under Coding agents."
        elif r["state"] == "empty":
            title, text = f"{r['name']} is out of credit", "Top it up to keep using its models."
        else:
            left = r.get("left")
            title = f"{r['name']} credit is low"
            text = (f"${left:.2f} left." if left is not None else "") + (f" {r['note']}." if r.get("note") else "")
        out.append({"key": f"credit-{r['id']}", "mark": r["state"], "title": title, "text": text.strip()})
    return out


def check(post: Optional[Post] = None, *, close: Optional[list[dict]] = None) -> tuple[bool, dict]:
    """Post a note for each limit newly close; `{told: [title…]}`."""
    if post is None:
        from . import notes

        post = notes.post
    close = gather() if close is None else close
    told = []
    with _LOCK:
        seen = _load()
        now_keys = {c["key"] for c in close}
        for c in close:
            if seen.get(c["key"]) == c["mark"]:
                continue
            post({"id": f"usage-{c['key']}"[:60], "title": c["title"], "text": c["text"],
                  "ttl_s": 12 * 3600})
            seen[c["key"]] = c["mark"]
            told.append(c["title"])
        # Fine again: told afresh next time it is close.
        for k in [k for k in seen if k not in now_keys]:
            del seen[k]
        _save(seen)
    return True, {"told": told}
