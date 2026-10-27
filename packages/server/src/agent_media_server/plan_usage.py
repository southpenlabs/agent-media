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
    extra = raw.get("extra_usage") or {}
    return {"windows": windows,
            "extra": ({"percent": extra.get("utilization"), "currency": extra.get("currency")}
                      if extra.get("is_enabled") else None)}


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
        return False, {"code": "unavailable", "error": f"usage not available: {e}", "status": 502}
    _cache = (now(), out)
    return True, out
