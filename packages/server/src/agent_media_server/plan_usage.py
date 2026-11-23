"""`/usage`: how much of the Claude plan is spent, for the app's card.

`/usage` typed from the phone goes into a terminal pane and draws a screen
nobody sees (core/slash_menu.py), so the numbers come from where the screen
gets them: Anthropic's OAuth usage endpoint, asked with the host's own Claude
login. The answer is trimmed to what a card needs — each window's percent
spent and when it resets — and kept for a minute, so a page that is opened and
closed does not ask again each time.

`windows` is a list of `{kind, label, percent, resets_at, severity}`; `kind`
is `session` (the 5-hour window), `weekly_all`, or `weekly_scoped` (one model's
share, named in `label`). `resets_at` is epoch seconds. `extra` is the paid
overflow when it is on, else null. A host with no login, or an answer that
does not parse, is `ok: false` with a `code`; the card then says nothing
rather than showing zeros.
"""

from __future__ import annotations

import json
import os
import time
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Optional

from . import auth
from agent_media_core import failures as _failures

URL = "https://api.anthropic.com/api/oauth/usage"
TTL_S = 60.0

_cache: tuple[float, dict] = (0.0, {})


def _token() -> Optional[str]:
    home = Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude")
    try:
        data = json.loads((home / ".credentials.json").read_text())
        return str(data["claudeAiOauth"]["accessToken"]) or None
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _fetch(token: str) -> dict:
    req = urllib.request.Request(URL, headers={
        "Authorization": f"Bearer {token}", "anthropic-beta": "oauth-2025-04-20"})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.load(r)


def _epoch(iso: object) -> Optional[int]:
    try:
        return int(datetime.fromisoformat(str(iso)).timestamp())
    except ValueError:
        return None


def shape(raw: dict) -> dict:
    """The card's view of the endpoint's answer."""
    windows = []
    for lim in raw.get("limits") or []:
        kind = lim.get("kind")
        if kind not in ("session", "weekly_all", "weekly_scoped") or lim.get("percent") is None:
            continue
        model = ((lim.get("scope") or {}).get("model") or {}).get("display_name")
        windows.append({
            "kind": kind, "label": model or None, "percent": int(lim["percent"]),
            "resets_at": _epoch(lim.get("resets_at")), "severity": lim.get("severity") or "normal"})
    return {"windows": windows, "extra": _extra(raw.get("extra_usage") or {})}


def _extra(extra: dict) -> Optional[dict]:
    """The paid overflow, when on: the month's limit, what is used and what
    is left, in the currency's units (the endpoint counts in its smallest,
    `decimal_places` of them), and whether the limit is reached. The app
    shows what is left once a window reaches 100% (David, 3 Nov 2026: "once
    usage gets to 100% display available usage credit until rate limits
    reset")."""
    if not extra.get("is_enabled"):
        return None
    try:
        places = int(extra.get("decimal_places") if extra.get("decimal_places") is not None else 2)
    except (TypeError, ValueError):
        places = 2
    unit = 10 ** places

    def money(v: object) -> Optional[float]:
        try:
            return round(float(v) / unit, places)
        except (TypeError, ValueError):
            return None

    limit, used = money(extra.get("monthly_limit")), money(extra.get("used_credits"))
    left = round(max(0.0, limit - used), places) if limit is not None and used is not None else None
    return {"percent": extra.get("utilization"), "currency": extra.get("currency"),
            "limit": limit, "used": used, "left": left, "decimal_places": places,
            "limit_reached": bool(extra.get("spend_limit_reached"))}


def get(bearer: str, *, fetch=_fetch, now=time.time) -> tuple[bool, dict]:
    global _cache
    ok, detail = auth.may_control_speech(bearer)
    if not ok:
        return False, detail
    at, kept = _cache
    if kept and now() - at < TTL_S:
        return True, kept
    token = _token()
    if not token:
        return False, {"code": "no_login", "error": "Claude is not signed in on the server", "status": 404}
    try:
        out = shape(fetch(token))
    except Exception as e:  # network, 401 (expired login), a changed shape
        _failures.swallowed(e)
        return False, {"code": "unavailable", "error": f"usage not available: {e}", "status": 502}
    _cache = (now(), out)
    return True, out


# --- a subscription signed in for opencode (`?plan=opencode-go`) -------------

#: The plans whose usage can be asked, by their opencode provider: where,
#: asked with the plan's own key (opencode_keys.py). OpenCode Go's answer
#: (measured 24 Nov 2026) is `{"usage": {"rolling"|"weekly"|"monthly":
#: {"status", "percent", "resetsAt"}}}`, the rolling one its 5-hour window.
PLAN_URLS = {"opencode-go": "https://opencode.ai/zen/go/v1/usage"}
_PLAN_KINDS = (("rolling", "session"), ("weekly", "weekly_all"), ("monthly", "monthly"))
WARN_AT = 80

_plan_cache: dict[str, tuple[float, dict]] = {}


def _plan_fetch(url: str, key: str) -> dict:
    # opencode.ai answers Python's own User-Agent with 403 (24 Nov 2026).
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {key}",
                                               "User-Agent": "agent-media"})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.load(r)


def shape_plan(raw: dict) -> dict:
    """A plan's answer as the card's windows: its 5-hour window as `session`,
    its week as `weekly_all`, its month as `monthly`. It sends no severity,
    so one at WARN_AT% or more, or not "ok", is a warning."""
    usage = raw.get("usage") if isinstance(raw.get("usage"), dict) else {}
    windows = []
    for theirs, kind in _PLAN_KINDS:
        w = usage.get(theirs)
        if not isinstance(w, dict) or w.get("percent") is None:
            continue
        pct = int(w["percent"])
        warn = pct >= WARN_AT or str(w.get("status") or "ok") != "ok"
        windows.append({"kind": kind, "label": None, "percent": pct,
                        "resets_at": _epoch(str(w.get("resetsAt") or "").replace("Z", "+00:00")),
                        "severity": "warning" if warn else "normal"})
    return {"windows": windows, "extra": None}


def get_plan(bearer: str, plan: str, *, fetch=_plan_fetch, now=time.time) -> tuple[bool, dict]:
    """`GET /usage?plan=<provider>`: that plan's usage, in `get`'s shape."""
    ok, detail = auth.may_control_speech(bearer)
    if not ok:
        return False, detail
    if plan not in PLAN_URLS:
        return False, {"code": "unknown_plan", "error": f"no usage for {plan!r}", "status": 400}
    at, kept = _plan_cache.get(plan, (0.0, {}))
    if kept and now() - at < TTL_S:
        return True, kept
    from agent_media_core import opencode_keys

    key = str((opencode_keys.stored().get(plan) or {}).get("key") or "")
    if not key:
        return False, {"code": "no_login", "error": f"{plan} is not signed in on the server", "status": 404}
    try:
        out = shape_plan(fetch(PLAN_URLS[plan], key))
    except Exception as e:  # network, 401 (a revoked key), a changed shape
        _failures.swallowed(e)
        return False, {"code": "unavailable", "error": f"usage not available: {e}", "status": 502}
    _plan_cache[plan] = (now(), out)
    return True, out
