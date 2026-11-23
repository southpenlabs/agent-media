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


# --- the other subscriptions (`?plan=…`) ------------------------------------

#: Each plan's usage, in the card's shape, from the login the host already
#: has for it (David, 24 Nov 2026: "do all that for codex and anti-gravity
#: too ... and any other subscriptions I have"):
#:
#: - `opencode-go`: OpenCode Go, the key kept from the Coding agents page
#:   (opencode_keys.py), `opencode.ai/zen/go/v1/usage` → `{"usage":
#:   {"rolling"|"weekly"|"monthly": {"status", "percent", "resetsAt"}}}`.
#: - `codex`: the ChatGPT plan Codex is signed in with (`$CODEX_HOME/
#:   auth.json`), `chatgpt.com/backend-api/wham/usage` → `rate_limit.
#:   primary_window` (5 hours) and `secondary_window` (a week), each
#:   `{used_percent, limit_window_seconds, reset_at}`; what Codex's /status reads.
#: - `antigravity`: the Google login the gateway's proxy keeps for it
#:   (`~/.cli-proxy-api/antigravity-*.json`, MEDIA_ANTIGRAVITY_AUTH),
#:   `cloudcode-pa…/v1internal:fetchAvailableModels` → per model
#:   `quotaInfo: {remainingFraction, resetTime}`, shown a family at a time
#:   (Claude, Gemini, GPT-OSS: the most used of its models, and its reset) as `model` windows.
#:
#: Measured 24 Nov 2026. A plan with no login here is not listed (`plans`).
WARN_AT = 80
UA = "agent-media"   # opencode.ai answers Python's own User-Agent with 403

_plan_cache: dict[str, tuple[float, dict]] = {}


class NoLogin(Exception):
    """The plan is not signed in on this host."""


def _ask(url: str, headers: dict, body: Optional[dict] = None, ua: str = UA) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, headers={
        **headers, "User-Agent": ua, **({"Content-Type": "application/json"} if data else {})})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.load(r)


def _window(kind: str, pct: float, resets_at: Optional[int], warn: bool = False,
            label: Optional[str] = None) -> dict:
    pct = int(round(pct))
    return {"kind": kind, "label": label, "percent": pct, "resets_at": resets_at,
            "severity": "warning" if warn or pct >= WARN_AT else "normal"}


def _iso(v: object) -> Optional[int]:
    return _epoch(str(v).replace("Z", "+00:00")) if v else None


# OpenCode Go

_PLAN_KINDS = (("rolling", "session"), ("weekly", "weekly_all"), ("monthly", "monthly"))


def shape_plan(raw: dict) -> dict:
    """OpenCode Go's answer: its 5-hour window as `session`, its week as
    `weekly_all`, its month as `monthly`. It sends no severity, so one at
    WARN_AT% or more, or not "ok", is a warning."""
    usage = raw.get("usage") if isinstance(raw.get("usage"), dict) else {}
    windows = []
    for theirs, kind in _PLAN_KINDS:
        w = usage.get(theirs)
        if not isinstance(w, dict) or w.get("percent") is None:
            continue
        windows.append(_window(kind, float(w["percent"]), _iso(w.get("resetsAt")),
                               warn=str(w.get("status") or "ok") != "ok"))
    return {"windows": windows, "extra": None}


def _go_key() -> str:
    from agent_media_core import opencode_keys

    return str((opencode_keys.stored().get("opencode-go") or {}).get("key") or "")


def _go() -> dict:
    key = _go_key()
    if not key:
        raise NoLogin
    return shape_plan(_ask("https://opencode.ai/zen/go/v1/usage", {"Authorization": f"Bearer {key}"}))


# Codex (the ChatGPT plan)

def _codex_auth() -> dict:
    from agent_media_core import harnesses

    try:
        tokens = json.loads((harnesses._codex_dir() / "auth.json").read_text()).get("tokens") or {}
    except (OSError, ValueError, AttributeError):
        return {}
    return tokens if tokens.get("access_token") else {}


def shape_codex(raw: dict) -> dict:
    """ChatGPT's rate limits: a 5-hour window as `session`, a week as
    `weekly_all` (by the window's own length, whichever it sends)."""
    rl = raw.get("rate_limit") if isinstance(raw.get("rate_limit"), dict) else {}
    windows = []
    for key in ("primary_window", "secondary_window"):
        w = rl.get(key)
        if not isinstance(w, dict) or w.get("used_percent") is None:
            continue
        secs = int(w.get("limit_window_seconds") or 0)
        kind = "session" if secs and secs <= 6 * 3600 else "weekly_all" if secs <= 8 * 86400 else "monthly"
        windows.append(_window(kind, float(w["used_percent"]),
                               int(w["reset_at"]) if w.get("reset_at") else None,
                               warn=bool(rl.get("limit_reached")) and float(w["used_percent"]) >= 100))
    return {"windows": windows, "extra": None}


def _codex() -> dict:
    t = _codex_auth()
    if not t:
        raise NoLogin
    return shape_codex(_ask("https://chatgpt.com/backend-api/wham/usage", {
        "Authorization": f"Bearer {t['access_token']}", "ChatGPT-Account-Id": str(t.get("account_id") or "")}))


# Antigravity

def _antigravity_auth() -> dict:
    import glob

    pattern = os.environ.get("MEDIA_ANTIGRAVITY_AUTH") or str(Path.home() / ".cli-proxy-api/antigravity-*.json")
    for f in sorted(glob.glob(pattern)):
        try:
            data = json.loads(Path(f).read_text())
        except (OSError, ValueError):
            continue
        if data.get("access_token") and not data.get("disabled"):
            return data
    return {}


def _family(name: str) -> str:
    """A model's family, its quota's: "Claude Opus 5.5 (High)" → Claude."""
    return name.split()[0] if name else ""


def shape_antigravity(raw: dict) -> dict:
    """Per-model quota, a family at a time: the most used of its models, and
    that model's reset. Models with no name (tab completion, its
    own helpers) are left out."""
    fams: dict[str, tuple[float, Optional[int]]] = {}
    for m in (raw.get("models") or {}).values():
        q = m.get("quotaInfo") if isinstance(m, dict) else None
        fam = _family(str(m.get("displayName") or "")) if isinstance(m, dict) else ""
        if not fam or not isinstance(q, dict) or q.get("remainingFraction") is None:
            continue
        used = 100.0 * (1.0 - float(q["remainingFraction"]))
        at = _iso(q.get("resetTime"))
        was = fams.get(fam)
        if was is None or used > was[0] or (used == was[0] and was[1] is None):
            fams[fam] = (used, at)
    return {"windows": [_window("model", used, at, label=fam)
                        for fam, (used, at) in sorted(fams.items())], "extra": None}


def _antigravity() -> dict:
    a = _antigravity_auth()
    if not a:
        raise NoLogin
    return shape_antigravity(_ask("https://cloudcode-pa.googleapis.com/v1internal:fetchAvailableModels",
                                  {"Authorization": f"Bearer {a['access_token']}"},
                                  {"project": str(a.get("project_id") or "")},
                                  ua="antigravity"))   # 403 to any other name (24 Nov 2026)


#: id → its name, the agent whose threads run on it, whether it is signed in
#: here, and the ask.
PLANS = {
    "opencode-go": {"name": "OpenCode Go", "agent": "opencode", "here": lambda: bool(_go_key()), "fetch": _go},
    "codex": {"name": "ChatGPT", "agent": "codex", "here": lambda: bool(_codex_auth()), "fetch": _codex},
    "antigravity": {"name": "Antigravity", "agent": "antigravity",
                    "here": lambda: bool(_antigravity_auth()), "fetch": _antigravity},
}


def plans(bearer: str) -> tuple[bool, dict]:
    """`GET /usage/plans`: the plans signed in here, `[{id, name, agent}]`."""
    ok, detail = auth.may_control_speech(bearer)
    if not ok:
        return False, detail
    out = []
    for pid, p in PLANS.items():
        try:
            here = p["here"]()
        except Exception as e:
            _failures.swallowed(e)
            here = False
        if here:
            out.append({"id": pid, "name": p["name"], "agent": p["agent"]})
    return True, {"plans": out}


def get_plan(bearer: str, plan: str, *, fetch=None, now=time.time) -> tuple[bool, dict]:
    """`GET /usage?plan=<id>`: that plan's usage, in `get`'s shape."""
    ok, detail = auth.may_control_speech(bearer)
    if not ok:
        return False, detail
    if plan not in PLANS:
        return False, {"code": "unknown_plan", "error": f"no usage for {plan!r}", "status": 400}
    at, kept = _plan_cache.get(plan, (0.0, {}))
    if kept and now() - at < TTL_S:
        return True, kept
    try:
        out = (fetch or PLANS[plan]["fetch"])()
    except NoLogin:
        return False, {"code": "no_login", "error": f"{PLANS[plan]['name']} is not signed in on the server",
                       "status": 404}
    except Exception as e:  # network, 401 (a revoked or expired login), a changed shape
        _failures.swallowed(e)
        return False, {"code": "unavailable", "error": f"usage not available: {e}", "status": 502}
    _plan_cache[plan] = (now(), out)
    return True, out
