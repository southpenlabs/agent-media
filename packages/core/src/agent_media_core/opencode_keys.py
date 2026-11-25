"""API keys for opencode's providers, set from the app's Coding agents page.

opencode's own free models need nothing; a key for a provider adds that
provider's free models to the sheet (opencode_models.py) — OpenRouter's
`…:free`, 50 requests a day on a free account, 1000 once it has had credit.
A plan's provider (`plan`: OpenCode Go) is a paid subscription instead: its
key puts every model of the plan on the sheet, for as long as it is kept.
opencode keeps keys in `auth.json` beside its database, one entry per
provider, `{"<provider>": {"type": "api", "key": "…"}}` — what `opencode auth
login` writes, and what `opencode auth list` counts (measured 28 Sep 2026 on
1.18.32: a key written there made OpenRouter's 17 free models appear).

A key is checked with the provider before it is kept, so a typo is said at
once rather than as a failed turn later. Written atomically, owner-only.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

#: The providers the page offers: their name, where a key is made, and the
#: URL that says whether a key is good (GET with it as the bearer).
PROVIDERS = {
    "openrouter": {
        "name": "OpenRouter",
        "signup": "https://openrouter.ai/keys",
        "check": "https://openrouter.ai/api/v1/key",
    },
    "opencode-go": {
        "name": "OpenCode Go",
        "signup": "https://opencode.ai/go",
        "check": "https://opencode.ai/zen/go/v1/models",
        "plan": "Go",
    },
}

_KEY = re.compile(r"[\x21-\x7e]{16,400}")


def auth_path() -> Path:
    from . import harnesses

    return harnesses.opencode_db().parent / "auth.json"


def _v2():
    """opencode 2's server when it keeps the keys (its credentials, not
    auth.json, which it does not read), else None."""
    from . import opencode_api

    return opencode_api.service() if opencode_api.installed_v2() else None


def stored() -> dict:
    """`{provider: {"type": "api", "key": …}}` as auth.json has it; on
    opencode 2 its active credentials, in that shape."""
    from . import opencode_api

    if opencode_api.installed_v2():
        srv = _v2()
        try:
            rows = srv.call("GET", "/api/credential", timeout=10) if srv else []
        except opencode_api.ApiError:
            rows = []
        return {str(c["integrationID"]): {"type": "api", "key": str(c["value"].get("key") or ""),
                                         "id": str(c.get("id") or "")}
                for c in rows or [] if isinstance(c, dict) and c.get("active")
                and isinstance(c.get("value"), dict) and c["value"].get("type") == "key"}
    try:
        data = json.loads(auth_path().read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def providers() -> list[dict]:
    """`[{id, name, signup, set, kind}]`, for the page; `kind` is "plan" for
    a subscription, "free" for a provider whose key adds free models. Never
    the key itself."""
    have = stored()
    return [{"id": pid, "name": p["name"], "signup": p["signup"],
             "set": isinstance(have.get(pid), dict) and bool(have[pid].get("key")),
             "kind": "plan" if p.get("plan") else "free"}
            for pid, p in PROVIDERS.items()]


def signed_in_plans() -> list[tuple[str, str]]:
    """`[(provider, note)]`: the plans a key is kept for, the sheet's reason
    to offer their models."""
    return [(row["id"], PROVIDERS[row["id"]]["plan"]) for row in providers()
            if row["kind"] == "plan" and row["set"]]


def well_formed(key: str) -> bool:
    return bool(_KEY.fullmatch(key or ""))


def check(provider: str, key: str, timeout: float = 10.0) -> tuple[bool | None, str]:
    """`(True, note)` a good key, `(False, why)` a refused one, `(None, why)`
    when the provider could not be asked."""
    p = PROVIDERS[provider]
    # opencode.ai answers Python's own User-Agent with 403, which read as a
    # refused key (24 Nov 2026), so every check names itself.
    req = urllib.request.Request(p["check"], headers={"Authorization": f"Bearer {key}",
                                                      "User-Agent": "agent-media"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            return False, f"{p['name']} refused this key"
        return None, f"{p['name']} answered {e.code}"
    except (OSError, ValueError) as e:
        return None, f"could not reach {p['name']} ({e.__class__.__name__})"
    if p.get("plan"):
        return True, f"Signed in: the {p['name']} models are on opencode's list"
    data = body.get("data") if isinstance(body, dict) else None
    if isinstance(data, dict) and data.get("is_free_tier"):
        return True, "Free account: its free models, 50 requests a day"
    return True, "Account with credit: its free models, 1000 requests a day"


def _write(data: dict) -> None:
    path = auth_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".auth.", suffix=".json")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2)
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _v2_add(srv, provider: str, key: str) -> None:
    srv.call("POST", "/api/credential", {"integrationID": provider, "label": provider,
                                         "value": {"type": "key", "key": key},
                                         "activate": True}, timeout=15)


def save(provider: str, key: str) -> None:
    """Keep `key` for `provider`, leaving every other provider's as it was.
    On opencode 2, as an active credential of its server (the old one of
    that provider removed)."""
    from . import opencode_api

    if opencode_api.installed_v2():
        srv = _v2()
        if srv is None:
            raise OSError("opencode's server is not running")
        old = stored().get(provider) or {}
        _v2_add(srv, provider, key)
        if old.get("id"):
            try:
                srv.call("DELETE", f"/api/credential/{old['id']}", timeout=15)
            except opencode_api.ApiError:
                pass
        return
    data = stored()
    data[provider] = {"type": "api", "key": key}
    _write(data)


def remove(provider: str) -> bool:
    from . import opencode_api

    data = stored()
    if provider not in data:
        return False
    if opencode_api.installed_v2():
        srv = _v2()
        if srv is None or not data[provider].get("id"):
            return False
        srv.call("DELETE", f"/api/credential/{data[provider]['id']}", timeout=15)
        return True
    del data[provider]
    _write(data)
    return True


def carry_over() -> list[str]:
    """On opencode 2: the keys auth.json holds that its server has no
    credential for, added to it (v2 does not read auth.json). The providers
    carried, by name. auth.json is left as it is."""
    from . import opencode_api

    if not opencode_api.installed_v2():
        return []
    srv = _v2()
    if srv is None:
        return []
    try:
        old = json.loads(auth_path().read_text())
    except (OSError, ValueError):
        return []
    have = stored()
    done = []
    for provider, entry in (old.items() if isinstance(old, dict) else []):
        if provider in have or not isinstance(entry, dict) or entry.get("type") != "api" \
                or not well_formed(str(entry.get("key") or "")):
            continue
        try:
            _v2_add(srv, provider, str(entry["key"]))
            done.append(provider)
        except opencode_api.ApiError:
            continue
    return done


def carry_to(srv) -> list[str]:
    """The user's keys (their own server's credentials, and auth.json's) into
    another opencode 2 server that has none for that provider: a private
    chat's home server (opencode_api.HomeService), whose database starts
    empty. The providers carried, by name."""
    from . import opencode_api

    try:
        rows = srv.call("GET", "/api/credential", timeout=10) or []
    except opencode_api.ApiError:
        return []
    have = {str(c.get("integrationID")) for c in rows if isinstance(c, dict) and c.get("active")}
    mine = dict(stored())
    try:
        old = json.loads(auth_path().read_text())
    except (OSError, ValueError):
        old = {}
    for provider, entry in (old.items() if isinstance(old, dict) else []):
        mine.setdefault(provider, entry)
    done = []
    for provider, entry in mine.items():
        key = str((entry or {}).get("key") or "") if isinstance(entry, dict) else ""
        if provider in have or (entry or {}).get("type") != "api" or not well_formed(key):
            continue
        try:
            _v2_add(srv, provider, key)
            done.append(provider)
        except opencode_api.ApiError:
            continue
    return done
