"""How much room each model on opencode's sheet has left, and the sheet in
that order (David, 24 Nov 2026: "It would be nice to somehow give models
with more credit, more priority" ... "can we also discuss getting a usage
indicator on the model list?").

A model's room comes from what pays for it:

- a plan (`opencode-go/…`): its most used window (plan_usage.py), so Go at
  35% of its 5 hours and 31% of its week has 65% left;
- prepaid credit or an API key, by the first part of its id that names one
  (`venice/…`, `openrouter/…`, `gateway/venice/…`; credit_usage.py): what is
  left in money, or only whether the key works;
- a free model: nothing to run out of but its own daily limit, which no
  provider reports.

Each row gains `group` (what pays for it, by name: "Go", "Venice", "Free",
or "" when nothing is known: a header over its rows) and `base` (its note
without the group's name), and `usage: {state, text, percent?, left?}` — `state` ok, near
(a plan window at plan_usage.WARN_AT% or credit under MEDIA_CREDIT_LOW), or
out (a window at 100%, credit used up or the key refused) — and its note
says it ("Go · 35% used", "$7.00 left"), so an app that only shows notes
shows it too. The sheet is then ordered: what is paid for and has room
(plans by most room, then credit by most money), the free models, then
those near a limit, then those out; each group keeps its own order.

`auto_default` is what a new chat on "Default" runs when opencode's own
default is near or out: the first model with room, else "" (opencode's own).
"""

from __future__ import annotations

import logging
from typing import Optional

log = logging.getLogger(__name__)

CREDIT_IDS = ("openrouter", "venice", "anthropic", "openai", "qwen")
PLAN_IDS = ("opencode-go",)


def source(model_id: str, note: str = "") -> tuple[str, str]:
    """`(kind, id)`: ("plan", "opencode-go"), ("credit", "venice"),
    ("free", ""), or ("", "") when nothing is known of what pays for it."""
    if note == "Free":
        return "free", ""
    parts = model_id.split("/")
    if parts[0] in PLAN_IDS:
        return "plan", parts[0]
    for p in parts:
        if p in CREDIT_IDS:
            return "credit", p
    return "", ""


def _plan_usage(plan: str) -> Optional[dict]:
    from . import plan_usage

    try:
        ok, out = plan_usage.plan_now(plan)
    except Exception as e:  # never let the sheet fail on a usage ask
        log.warning("headroom: %s usage failed (%s)", plan, e)
        return None
    windows = out.get("windows") or [] if ok else []
    if not windows:
        return None
    used = max(int(w.get("percent") or 0) for w in windows)
    state = "out" if used >= 100 else "near" if used >= plan_usage.WARN_AT else "ok"
    return {"state": state, "percent": used, "text": f"{used}% used"}


def _credit_rows() -> dict[str, dict]:
    from . import credit_usage

    try:
        return {r["id"]: r for r in credit_usage.found()}
    except Exception as e:
        log.warning("headroom: credit failed (%s)", e)
        return {}


def _credit_usage(row: Optional[dict]) -> Optional[dict]:
    if not row:
        return None
    state = {"ok": "ok", "low": "near", "empty": "out", "rejected": "out"}.get(row["state"])
    if not state:
        return None
    if row["state"] == "rejected":
        text = "key refused"
    elif row["state"] == "empty":
        text = "out of credit"
    elif row.get("left") is not None:
        cur = "$" if (row.get("currency") or "USD") == "USD" else f"{row['currency']} "
        text = f"{cur}{row['left']:.2f} left"
    else:
        text = ""
    out = {"state": state, "text": text}
    if row.get("left") is not None:
        out["left"] = row["left"]
    return out


def annotate(offered: list[dict]) -> list[dict]:
    """The sheet's rows with `usage` and their note saying it, in headroom
    order. A row nothing is known of is left as it is."""
    plans: dict[str, Optional[dict]] = {}
    credits: Optional[dict[str, dict]] = None
    rows = []
    for i, m in enumerate(offered):
        kind, sid = source(m["id"], m.get("note") or "")
        usage = None
        if kind == "plan":
            if sid not in plans:
                plans[sid] = _plan_usage(sid)
            usage = plans[sid]
        elif kind == "credit":
            if credits is None:
                credits = _credit_rows()
            usage = _credit_usage(credits.get(sid))
        row = dict(m)
        note = m.get("note") or ""
        # The group's name, for a header over its rows, and the note without
        # it (`base`: "Chat only", or nothing), for a row under that header.
        if kind == "plan":
            group = _plan_name(sid, note)
        elif kind == "credit":
            from . import credit_usage

            group = str((credit_usage.PROVIDERS.get(sid) or {}).get("name") or sid)
        else:
            group = "Free" if kind == "free" else ""
        row["group"] = group
        row["base"] = "" if note == group else note
        if usage:
            row["usage"] = usage
            if usage.get("text"):
                row["note"] = " · ".join(x for x in (note, usage["text"]) if x)
        rows.append((_rank(kind, usage), group, i, row))
    rows.sort(key=lambda r: (r[0], r[1], r[2]))
    return [r for *_, r in rows]


def _plan_name(plan: str, note: str) -> str:
    """A plan's short name for its header: the note its rows carry ("Go"),
    else the plan's own name."""
    if note:
        return note
    from . import plan_usage

    return str((plan_usage.PLANS.get(plan) or {}).get("name") or plan)


def _rank(kind: str, usage: Optional[dict]) -> tuple:
    state = (usage or {}).get("state", "ok")
    if state == "out":
        return (4, 0)
    if state == "near":
        return (3, 0)
    if kind == "plan" and usage:
        return (0, usage.get("percent", 0))          # most room first
    if kind == "credit" and usage:
        return (1, -float(usage.get("left") or 0))   # most money first
    if kind == "free":
        return (2, 0)
    return (2, 1)                                     # nothing known: after the free ones


def auto_default(offered: list[dict], configured: str) -> str:
    """The model a new chat on "Default" runs when `configured` (opencode's
    own default) is near or out of room: the first with room on the
    annotated sheet. "" to leave opencode's own."""
    mine = next((m for m in offered if m["id"] == configured), None)
    if not mine or (mine.get("usage") or {}).get("state", "ok") == "ok":
        return ""
    best = next((m for m in offered if (m.get("usage") or {}).get("state", "ok") == "ok"), None)
    return best["id"] if best else ""
