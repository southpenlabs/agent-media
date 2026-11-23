"""`/usage/credits`: the prepaid credit and API keys the host holds, for the
Coding agents page beside the plans' usage (plan_usage.py).

Found, not configured (David, 24 Nov 2026: "let's add the prepaid ones too
... automatically if possible ... including those with API keys? and how
about qwen?"): a provider is listed when a key for it is found in the
environment, among opencode's credentials (opencode_keys.py), in the
gateway's env file (`~/.config/litellm/litellm.env`, the same key store
speech-to-text reads) or in the shell's (`~/.zsh_secrets`, where
dotfiles-secrets puts every key). MEDIA_CREDIT_KEY_FILES names other files.

What each can say (measured 24 Nov 2026):

- OpenRouter: `GET /api/v1/credits` → bought and used, so what is left.
- Venice: `GET /api/v1/api_keys/rate_limits` → `balances` in USD and DIEM.
- Anthropic API: no balance for an ordinary key; a one-token Haiku request
  says whether there is credit at all (asked every six hours, a fraction of
  a cent a day), as login-watch does.
- OpenAI API: no balance either; `GET /v1/models` says the key works.
- Qwen (Alibaba Model Studio, DASHSCOPE_API_KEY or QWEN_API_KEY): no
  balance; its OpenAI-compatible `/models` says the key works.

`state` is `ok`, `low` (under MEDIA_CREDIT_LOW, US$1 by default), `empty`,
`rejected` (the provider refused the key) or `unknown` (it did not answer).
Never the key itself.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable, Optional

from . import auth
from agent_media_core import failures as _failures

UA = "agent-media"
_cache: dict[str, tuple[float, dict]] = {}


def low_at() -> float:
    try:
        return float(os.environ.get("MEDIA_CREDIT_LOW") or 1.0)
    except ValueError:
        return 1.0


# --- where keys are -----------------------------------------------------------

def _files() -> list[Path]:
    extra = [p for p in (os.environ.get("MEDIA_CREDIT_KEY_FILES") or "").split(":") if p]
    return [Path(os.path.expanduser(p)) for p in
            (*extra, "~/.config/litellm/litellm.env", "~/.zsh_secrets")]


def _from_file(path: Path, var: str) -> str:
    try:
        lines = path.read_text().splitlines()
    except OSError:
        return ""
    for line in lines:
        line = line.strip()
        if line.startswith("export "):
            line = line[7:].lstrip()
        if line.startswith(var + "="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    return ""


def find_key(variables: tuple[str, ...], opencode: str = "") -> str:
    """The first key found: the environment, opencode's credential for
    `opencode`, then the key files, each in turn."""
    for var in variables:
        if os.environ.get(var, "").strip():
            return os.environ[var].strip()
    if opencode:
        try:
            from agent_media_core import opencode_keys

            got = str((opencode_keys.stored().get(opencode) or {}).get("key") or "")
        except Exception as e:  # opencode's server down: the files still say
            _failures.swallowed(e)
            got = ""
        if got:
            return got
    for path in _files():
        for var in variables:
            got = _from_file(path, var)
            if got:
                return got
    return ""


# --- asking -------------------------------------------------------------------

def _ask(method: str, url: str, headers: dict, body: Optional[dict] = None) -> tuple[int, dict, str]:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={
        **headers, "User-Agent": UA, **({"Content-Type": "application/json"} if data else {})})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            text = r.read().decode("utf-8", "replace")
            status = r.status
    except urllib.error.HTTPError as e:
        text, status = e.read().decode("utf-8", "replace"), e.code
    try:
        parsed = json.loads(text) if text else {}
    except ValueError:
        parsed = {}
    return status, parsed if isinstance(parsed, dict) else {}, text


def _row(state: str, **kw) -> dict:
    return {"state": state, "currency": None, "left": None, "limit": None, "used": None,
            "note": "", **kw}


def _refused(status: int) -> Optional[dict]:
    if status in (401, 403):
        return _row("rejected", note="The key was refused")
    if status != 200:
        return _row("unknown", note=f"Did not answer (HTTP {status})")
    return None


def _money_state(left: float) -> str:
    return "empty" if left <= 0 else "low" if left < low_at() else "ok"


def openrouter(key: str) -> dict:
    status, d, _ = _ask("GET", "https://openrouter.ai/api/v1/credits", {"Authorization": f"Bearer {key}"})
    bad = _refused(status)
    if bad:
        return bad
    data = d.get("data") or {}
    total, used = float(data.get("total_credits") or 0), float(data.get("total_usage") or 0)
    left = round(total - used, 2)
    return _row(_money_state(left), currency="USD", left=left, limit=round(total, 2), used=round(used, 2))


def venice(key: str) -> dict:
    status, d, _ = _ask("GET", "https://api.venice.ai/api/v1/api_keys/rate_limits",
                        {"Authorization": f"Bearer {key}"})
    bad = _refused(status)
    if bad:
        return bad
    data = d.get("data") or {}
    bal = data.get("balances") or {}
    usd, diem = float(bal.get("USD") or 0), float(bal.get("DIEM") or 0)
    state = "empty" if data.get("accessPermitted") is False or (usd <= 0 and diem <= 0) \
        else "low" if usd < low_at() and diem <= 0 else "ok"
    return _row(state, currency="USD", left=round(usd, 2), note=f"{diem:g} DIEM" if diem else "")


def anthropic(key: str) -> dict:
    status, d, text = _ask("POST", "https://api.anthropic.com/v1/messages",
                           {"x-api-key": key, "anthropic-version": "2023-06-01"},
                           {"model": "claude-haiku-5-5", "max_tokens": 1,
                            "messages": [{"role": "user", "content": "hi"}]})
    if status == 200:
        return _row("ok", note="Has credit; the balance is not reported")
    if "credit balance" in text.lower():
        return _row("empty", note="Out of credit")
    return _refused(status) or _row("unknown")


def _models(url: str) -> Callable[[str], dict]:
    def ask(key: str) -> dict:
        status, _, text = _ask("GET", url, {"Authorization": f"Bearer {key}"})
        if status == 429 and "quota" in text.lower():
            return _row("empty", note="Out of quota")
        return _refused(status) or _row("ok", note="The key works; the balance is not reported")
    return ask


#: id → name, where its key is (variables, opencode provider), how to ask,
#: and how long an answer is kept.
PROVIDERS: dict[str, dict] = {
    "openrouter": {"name": "OpenRouter", "vars": ("OPENROUTER_API_KEY",), "opencode": "openrouter",
                   "ask": openrouter, "ttl": 300},
    "venice": {"name": "Venice", "vars": ("VENICE_API_KEY",), "opencode": "venice",
               "ask": venice, "ttl": 300},
    "anthropic": {"name": "Anthropic API", "vars": ("ANTHROPIC_API_KEY",), "opencode": "",
                  "ask": anthropic, "ttl": 6 * 3600},
    "openai": {"name": "OpenAI API", "vars": ("OPENAI_API_KEY",), "opencode": "",
               "ask": _models("https://api.openai.com/v1/models"), "ttl": 3600},
    "qwen": {"name": "Qwen", "vars": ("DASHSCOPE_API_KEY", "QWEN_API_KEY"), "opencode": "alibaba",
             "ask": _models("https://dashscope-intl.aliyuncs.com/compatible-mode/v1/models"), "ttl": 3600},
}


def credits(bearer: str, *, now=time.time) -> tuple[bool, dict]:
    """`GET /usage/credits`: `[{id, name, state, currency, left, limit, used,
    note}]`, one per provider a key is found for."""
    ok, detail = auth.may_control_speech(bearer)
    if not ok:
        return False, detail
    out = []
    for pid, p in PROVIDERS.items():
        key = find_key(p["vars"], p["opencode"])
        if not key:
            continue
        at, kept = _cache.get(pid, (0.0, {}))
        if not kept or now() - at >= p["ttl"]:
            try:
                kept = p["ask"](key)
            except Exception as e:  # network down, a changed answer
                _failures.swallowed(e)
                kept = _row("unknown", note="Did not answer")
            _cache[pid] = (now(), kept)
        out.append({"id": pid, "name": p["name"], **kept})
    return True, {"credits": out, "low_at": low_at()}
