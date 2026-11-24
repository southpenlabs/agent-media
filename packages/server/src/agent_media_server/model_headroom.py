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

The other agents' sheets get the same header and usage, in their own order
(`grouped`; David, 25 Nov 2026: "It might be nice to add it for lists
without groups too"): Claude's models under the Claude plan's most used of
its 5 hours and week, Codex's under ChatGPT's, Antigravity's a family at a
time (its quota is per family), pi's by provider (meridian is the Claude
plan; venice, openrouter and the API keys their credit). A sheet nothing is
known of (grok's, a host with no login) is left with no groups at all.

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
    return _windows_usage(out.get("windows") or [] if ok else [], plan_usage.WARN_AT)


def resets(at: Optional[float], now: Optional[float] = None) -> str:
    """When a limit resets, in the host's time (David, 25 Nov 2026: "rather
    than just the percentage, could we also say when it resets?"): "resets
    4:00 PM" today, "resets Thu 4:00 PM" within the week, else "resets 16 Dec"."""
    import time as _t

    if not at:
        return ""
    now = _t.time() if now is None else now
    when, today = _t.localtime(at), _t.localtime(now)
    if when.tm_yday == today.tm_yday and when.tm_year == today.tm_year:
        return "resets " + _t.strftime("%-I:%M %p", when)
    if at - now < 6.5 * 86400:
        return "resets " + _t.strftime("%a %-I:%M %p", when)
    return "resets " + _t.strftime("%-d %b", when)


def _with_reset(text: str, at: Optional[float]) -> str:
    r = resets(at)
    return f"{text} · {r}" if r else text


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
    cap = row.get("cap") or {}
    if row["state"] in ("ok", "low") and cap and row.get("left") is not None and cap["remaining"] < row["left"]:
        # The key's spending cap stops it before the credit does.
        text = f"${cap['remaining']:.2f} left {'this week' if cap.get('every') == 'weekly' else 'under its cap'}"
        text = _with_reset(text, _iso_epoch(cap.get("resets_at")))
    out = {"state": state, "text": text}
    if row.get("left") is not None:
        out["left"] = min(row["left"], cap["remaining"]) if cap else row["left"]
    return out


def _free_daily(row: Optional[dict]) -> Optional[dict]:
    """OpenRouter's free models' daily requests: what is left today."""
    free = (row or {}).get("free_daily")
    if not free:
        return None
    left, limit = int(free["remaining"]), int(free["limit"]) or 1
    state = "out" if left <= 0 else "near" if left < limit * 0.2 else "ok"
    # OpenRouter's day starts at midnight UTC.
    import time as _t

    midnight = (int(_t.time()) // 86400 + 1) * 86400
    return {"state": state, "percent": round(100 * (limit - left) / limit),
            "text": _with_reset(f"{left:,} left today", midnight)}


def _iso_epoch(v) -> Optional[float]:
    from datetime import datetime

    try:
        return datetime.fromisoformat(str(v).replace("Z", "+00:00")).timestamp() if v else None
    except ValueError:
        return None


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
        elif kind == "free" and m["id"].startswith("openrouter/"):
            # OpenRouter's free models share a daily allowance of their own.
            group = "OpenRouter free"
            if credits is None:
                credits = _credit_rows()
            usage = _free_daily(credits.get("openrouter"))
        else:
            group = "Free" if kind == "free" else ""
        row["group"] = group
        row["base"] = "" if note == group else note
        if kind in ("plan", "credit"):
            # How fast it goes through tokens, for `weigh` (the row's weight).
            row["_blended"] = _blend(opencode_models_cost(m["id"]))
        if kind == "credit":
            # Paid per use: its price is what spends the credit (David, 25 Nov
            # 2026: "the token rate for each"). Not on a plan's or a free
            # model's row, where it is noise.
            price = _price(m["id"])
            if price:
                row["base"] = " · ".join(x for x in (row["base"], price) if x)
                note = " · ".join(x for x in (note, price) if x)
                row["note"] = note
        if usage:
            row["usage"] = usage
            if usage.get("text"):
                row["note"] = " · ".join(x for x in (note, usage["text"]) if x)
        rows.append((_rank(kind, usage), group, i, row))
    rows.sort(key=lambda r: (r[0], r[1], r[2]))
    return weigh([r for *_, r in rows])


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


# --- the other agents' sheets -------------------------------------------------

def _windows_usage(windows: list[dict], warn_at: int) -> Optional[dict]:
    """The most used window's percent and when it resets ("35% used ·
    resets 4:00 PM"): the limit that will stop it first."""
    if not windows:
        return None
    top = max(windows, key=lambda w: int(w.get("percent") or 0))
    used = int(top.get("percent") or 0)
    state = "out" if used >= 100 else "near" if used >= warn_at else "ok"
    at = top.get("resets_at")
    return {"state": state, "percent": used, "text": _with_reset(f"{used}% used", at),
            **({"resets_at": at} if at else {})}


def _claude_windows() -> list[dict]:
    from . import plan_usage

    try:
        ok, out = plan_usage.claude_now()
    except Exception as e:
        log.warning("headroom: claude usage failed (%s)", e)
        return []
    return out.get("windows") or [] if ok else []


def _claude_usage() -> Optional[dict]:
    """The Claude plan's room: the most used of its 5 hours and its week."""
    from . import plan_usage

    return _windows_usage([w for w in _claude_windows() if w.get("kind") != "weekly_scoped"],
                          plan_usage.WARN_AT)


def _claude_own_weeks() -> dict[str, dict]:
    """A model's own week on top of the plan's (Fable's, 25 Nov 2026: "doesn't
    Fable have a different limit? why isn't that limit displayed?"), by the
    name the plan gives it: its own percent in the words, its state the worse
    of its week and the plan's, since either stops it."""
    from . import plan_usage

    windows = _claude_windows()
    plan = _windows_usage([w for w in windows if w.get("kind") != "weekly_scoped"], plan_usage.WARN_AT)
    order = {"ok": 0, "near": 1, "out": 2}
    out = {}
    for w in windows:
        if w.get("kind") != "weekly_scoped" or not w.get("label"):
            continue
        own = _windows_usage([w], plan_usage.WARN_AT)
        if not own:
            continue
        state = max(own["state"], (plan or {}).get("state", "ok"), key=order.get)
        out[str(w["label"])] = {"state": state, "percent": own["percent"],
                                "text": _with_reset(f"{own['percent']}% of its week used", w.get("resets_at"))}
    return out


def _family_usage() -> dict[str, dict]:
    """Antigravity's room a family at a time: {"Claude": usage, …}."""
    from . import plan_usage

    try:
        ok, out = plan_usage.plan_now("antigravity")
    except Exception as e:
        log.warning("headroom: antigravity usage failed (%s)", e)
        return {}
    return {str(w.get("label")): u for w in (out.get("windows") or [] if ok else [])
            if (u := _windows_usage([w], plan_usage.WARN_AT))}


def grouped(agent: str, offered: list[dict]) -> list[dict]:
    """`offered` (Claude's, Codex's, Antigravity's or pi's sheet) with
    `group`, `base` and `usage` per row, a group's rows brought together in
    the order the groups first appear. Unchanged when nothing is known of
    any row, so the sheet is drawn with no headers."""
    if not offered or agent == "opencode":
        return offered
    names: dict[int, str] = {}
    usages: dict[str, Optional[dict]] = {}
    if agent == "claude":
        usages["Claude"] = _claude_usage()
        own = _claude_own_weeks()
        usages.update(own)
        # A model with a week of its own (Fable) under its own header.
        names = {i: next((n for n in own if str(m.get("label") or m["id"]).lower().startswith(n.lower())), "Claude")
                 for i, m in enumerate(offered)}
    elif agent == "codex":
        usages["ChatGPT"] = _plan_usage("codex")
        names = {i: "ChatGPT" for i in range(len(offered))}
        # A model with a limit of its own, or held back until a time, under
        # its own header (as Claude's Fable).
        for i, m in enumerate(offered):
            own = _codex_own(m)
            if own:
                usages[own[0]] = own[1]
                names[i] = own[0]
    elif agent == "antigravity":
        usages.update(_family_usage())
        names = {i: g for i, m in enumerate(offered)
                 if (g := str(m.get("label") or m["id"]).split()[0]) in usages}
    elif agent == "pi":
        credits: Optional[dict[str, dict]] = None
        for i, m in enumerate(offered):
            provider = str(m.get("provider") or m["id"].split("/")[0])
            if provider == "meridian":            # Claude Max through the proxy
                if "Claude" not in usages:
                    usages["Claude"] = _claude_usage()
                    usages.update(_claude_own_weeks())
                label = str(m.get("label") or m["id"]).lower()
                names[i] = next((n for n in usages if n != "Claude" and usages[n]
                                 and n.lower() in label.replace("claude-", "")), "Claude")
                continue
            kind, sid = source(m["id"])
            if kind == "plan":
                group = _plan_name(sid, "")
                if group not in usages:
                    usages[group] = _plan_usage(sid)
            elif kind == "credit":
                from . import credit_usage

                if credits is None:
                    credits = _credit_rows()
                group = str((credit_usage.PROVIDERS.get(sid) or {}).get("name") or sid)
                usages.setdefault(group, _credit_usage(credits.get(sid)))
            else:
                group = provider
            names[i] = group
    else:
        return offered
    from . import credit_usage as _cu

    credit_groups = {str(p.get("name")) for p in _cu.PROVIDERS.values()}
    if not any(usages.get(g) for g in names.values()):
        # No usage known (a busy usage endpoint, no login): no headers, but
        # each model's rate still shows (25 Nov 2026: the bars went with the
        # headers when Anthropic answered 429).
        plain = []
        for i, m in enumerate(offered):
            row = dict(m)
            row["_blended"] = _blend(_row_cost(m, names.get(i, "") or "priced", credit_groups))
            plain.append(row)
        return weigh(plain)
    order: list[str] = []
    rows = []
    for i, m in enumerate(offered):
        group = names.get(i, "")
        if group not in order:
            order.append(group)
        row = dict(m)
        note = str(m.get("note") or "")
        provider = str(m.get("provider") or "")
        # The note without the provider's name in front ("meridian · 200K context").
        base = note[len(provider):].lstrip(" ·") if provider and note.startswith(provider) else note
        row["group"], row["base"] = group, "" if base == group else base
        # What it costs: a price where it is paid per use, else a weight on
        # the plan (cheap, medium, expensive); nothing when nothing prices it.
        cost = _row_cost(m, group, credit_groups)
        row["_blended"] = _blend(cost)
        if group in credit_groups:
            price = _price_text(cost)
            if price:
                row["base"] = " · ".join(x for x in (row["base"], price) if x)
        if usages.get(group):
            row["usage"] = usages[group]
        rows.append((order.index(group), i, row))
    if agent == "pi":
        # pi's providers in headroom order, as opencode's sheet (25 Nov 2026):
        # room first (a plan by most room, credit by most money), then those
        # nothing is known of, then near a limit, then out.
        rank = {g: _group_rank(usages.get(g)) for g in order}
        rows.sort(key=lambda r: (rank[order[r[0]]], r[0], r[1]))
    else:
        rows.sort(key=lambda r: (r[0], r[1]))
    return weigh([r for *_, r in rows])


def _group_rank(usage: Optional[dict]) -> tuple:
    state = (usage or {}).get("state")
    if state == "out":
        return (4, 0)
    if state == "near":
        return (3, 0)
    if usage and usage.get("left") is not None:
        return (1, -float(usage["left"]))
    if usage and usage.get("percent") is not None:
        return (0, usage["percent"])
    return (2, 0)


def _codex_out() -> dict:
    from . import plan_usage

    try:
        ok, out = plan_usage.plan_now("codex")
    except Exception as e:
        log.warning("headroom: codex usage failed (%s)", e)
        return {}
    return out if ok else {}


def _when(at: Optional[int]) -> str:
    import time as _t

    return _t.strftime("%a %-I:%M %p", _t.localtime(at)) if at else ""


def _codex_own(m: dict) -> Optional[tuple[str, dict]]:
    """`(header, usage)` for a Codex model with a limit of its own: held back
    ("back Thu 4:00 PM"), or a window of its own ("12% of its week used",
    its state the worse of that and the plan's)."""
    from . import plan_usage

    out = _codex_out()
    mid, label = str(m["id"]), str(m.get("label") or m["id"])
    status = (out.get("models") or {}).get(mid)
    if status and not status.get("available", True):
        when = _when(status.get("available_at"))
        return label, {"state": "out", "percent": 100, "text": f"back {when}" if when else "not available now"}
    plan = _windows_usage([w for w in out.get("windows") or [] if w.get("kind") != "weekly_scoped"],
                          plan_usage.WARN_AT)
    order = {"ok": 0, "near": 1, "out": 2}
    for w in out.get("windows") or []:
        name = str(w.get("label") or "")
        if w.get("kind") != "weekly_scoped" or not name:
            continue
        if name.lower() in (mid.lower(), label.lower()) or mid.lower().startswith(name.lower()):
            own = _windows_usage([w], plan_usage.WARN_AT)
            if own:
                state = max(own["state"], (plan or {}).get("state", "ok"), key=order.get)
                return label, {"state": state, "percent": own["percent"],
                               "text": _with_reset(f"{own['percent']}% of its own limit used",
                                                   w.get("resets_at"))}
    return None


# --- a turn that ran out: retry on the roomiest -------------------------------
#
# David, 24-25 Nov 2026: "Switch model when one runs out" (do all). When an
# opencode turn fails on a used-up limit (429) or no credit (402), the thread
# offers one tap, "Retry on <model>": the model with the most room that is
# not the one that failed nor in its used-up group. The tap switches the
# thread to it and asks opencode to try again (send.reply). An offer, never a
# silent switch, so a model never changes under the person.

RETRY_CODES = (429, 402)
RETRY_PREFIX = "Retry on "
#: session → (failed message id, model id, label): the offer for the turn that failed last.
_RETRY: dict[str, tuple[str, str, str]] = {}


def retry_pick(failed: str) -> Optional[tuple[str, str]]:
    """`(id, label)` of the model to offer instead of `failed`, or None."""
    from agent_media_core import opencode_models

    sheet = annotate(opencode_models.models()[0])
    mine = next((m for m in sheet if m["id"] == failed), None)
    spent = (mine or {}).get("group") if (mine or {}).get("usage") else None
    for m in sheet:
        if m["id"] == failed or (spent and m.get("group") == spent):
            continue
        if (m.get("usage") or {}).get("state", "ok") != "ok":
            continue
        return m["id"], m["label"]
    return None


def note_turn(session: str, message_id: str, error, failed_model: str) -> None:
    """Called with the last message of an opencode thread as it is read: a
    limit or credit failure keeps an offer for it; anything else drops it."""
    code = ((error or {}).get("data") or {}).get("statusCode") if isinstance(error, dict) else None
    held = _RETRY.get(session)
    if code not in RETRY_CODES:
        _RETRY.pop(session, None)
        return
    if held and held[0] == message_id:
        return
    try:
        pick = retry_pick(failed_model)
    except Exception as e:
        log.warning("headroom: retry pick failed (%s)", e)
        pick = None
    if pick:
        _RETRY[session] = (message_id, pick[0], pick[1])
    else:
        _RETRY.pop(session, None)


def retry_chip(session: str) -> str:
    """"Retry on <label>" when the thread's last turn failed on a limit, else ""."""
    held = _RETRY.get(session)
    return RETRY_PREFIX + held[2] if held else ""


def take_retry(session: str, text: str) -> str:
    """The model id to switch to when `text` is the offer's own words, else "".
    The offer is used up by it."""
    held = _RETRY.get(session)
    if held and text.strip() == RETRY_PREFIX + held[2]:
        _RETRY.pop(session, None)
        return held[1]
    return ""


def _money(v: float) -> str:
    return f"${v:.2f}".rstrip("0").rstrip(".") if v >= 0.1 else f"${v:.3f}".rstrip("0")


def _price(model: str) -> str:
    """"$0.33 in · $1.32 out per M" for a priced model, else ""."""
    from agent_media_core import opencode_models

    i, o = opencode_models.cost_of(model)
    if not i and not o:
        return ""
    return f"{_money(i)} in · {_money(o)} out per M"


#: The bands of `_tier`, by a price blended three parts input to one output
#: per million tokens (a coding turn reads far more than it writes): Haiku
#: and the flash models cheap, Sonnet and most of Go medium, Opus, Fable,
#: GPT-6.2 Astra and Kimi K3 expensive (25 Nov 2026).
CHEAP_UNDER, EXPENSIVE_FROM = 0.5, 5.0


def _tier(cost: tuple[float, float]) -> str:
    """"Cheap", "Medium cost" or "Expensive" for a priced model, else ""
    (David, 25 Nov 2026: "Maybe cheap medium expensive can be used when a
    dollar amount doesn't work")."""
    i, o = cost
    if not i and not o:
        return ""
    blended = (3 * i + o) / 4
    return "Cheap" if blended < CHEAP_UNDER else "Expensive" if blended >= EXPENSIVE_FROM else "Medium cost"


def opencode_models_cost(model: str) -> tuple[float, float]:
    from agent_media_core import opencode_models

    return opencode_models.cost_of(model)


def _name_for_price(m: dict) -> str:
    """The name a model goes by in the listings: Claude Code's "Opus 5.5" is
    claude-opus-5-5; Codex's and pi's ids are their own."""
    mid = str(m["id"]).rsplit("/", 1)[-1]
    label = str(m.get("label") or "")
    if "/" not in str(m["id"]) and "-" not in mid and label:   # a Claude alias
        return "claude-" + label.lower().replace(" ", "-").replace(".", "-")
    return mid


def _row_cost(m: dict, group: str, credit_groups: set[str]) -> tuple[float, float]:
    """A row's price per million tokens: its own where it is paid per use or
    on Go, else its model's by name in the listings (a Claude alias, a Codex
    id); (0, 0) when nothing prices it."""
    from agent_media_core import opencode_models

    if not opencode_models._costs:
        try:
            opencode_models.models()           # the listing, cached ten minutes
        except Exception as e:
            log.warning("headroom: listing for prices failed (%s)", e)
            return (0.0, 0.0)
    if not group:
        return (0.0, 0.0)
    if group in credit_groups or ("/" in str(m["id"]) and group == "Go"):
        own = opencode_models.cost_of(m["id"])
        if own != (0.0, 0.0) or group in credit_groups:
            return own
    return opencode_models.cost_by_name(_name_for_price(m))


CHEAP_RATIO, EXPENSIVE_RATIO = 0.5, 2.0


def _blend(cost: tuple[float, float]) -> Optional[float]:
    i, o = cost
    return (3 * i + o) / 4 if (i or o) else None


def _price_text(cost: tuple[float, float]) -> str:
    i, o = cost
    return f"{_money(i)} in · {_money(o)} out per M" if (i or o) else ""


def weigh(rows: list[dict]) -> list[dict]:
    """Each priced row's `weight`: how fast it goes through tokens beside the
    rest of its sheet (David, 25 Nov 2026: "a line under each model ... how
    quickly it goes through tokens in relation to the rest ... green, orange
    and red"). `{tier, ratio, scale}`: `ratio` its price over the sheet's
    median ("4× average"), `tier` cheap (CHEAP_RATIO or less), expensive
    (EXPENSIVE_RATIO or more) or medium by that ratio, `scale`
    0..1 where it sits between the sheet's cheapest and dearest on a log
    scale (a bar's length). A row nothing prices has none."""
    import math

    priced = sorted(r["_blended"] for r in rows if r.get("_blended"))
    if priced:
        mid = priced[len(priced) // 2] if len(priced) % 2 else (priced[len(priced) // 2 - 1] + priced[len(priced) // 2]) / 2
        lo, hi = math.log(priced[0]), math.log(priced[-1])
    for r in rows:
        b = r.pop("_blended", None)
        if not b or not priced:
            continue
        ratio = b / mid if mid else 1.0
        # Against the rest of the sheet, as the multiplier says it: half the
        # typical rate or less cheap, double or more expensive.
        tier = "cheap" if ratio <= CHEAP_RATIO else "expensive" if ratio >= EXPENSIVE_RATIO else "medium"
        scale = (math.log(b) - lo) / (hi - lo) if hi > lo else 0.5
        r["weight"] = {"tier": tier, "ratio": round(ratio, 1) if ratio >= 1 else round(ratio, 2),
                       "scale": round(scale, 2)}
    return rows
