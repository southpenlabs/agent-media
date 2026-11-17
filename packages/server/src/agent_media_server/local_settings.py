"""The phone bridges' three settings (server-contract.md §6.61 "Settings").

docs/proposals/2026-11-17-phone-bridges-server-integration.md, "Refined"
(David, 18 Nov 2026: "Do the settings link"): **what leaves the phone**
(`settings` only / an `encrypted` copy / a `full` copy), **backup** (to this
`server`, to Google `drive` through Android's backup, or `off`), and **apply
to the networks** (one switch, with per-network exceptions). The companion
and Sasonica's Settings → Bridges both read and write them here, so every
device shows the same and the phone follows them.

**One per server**, not per account: Local chats are kept per server
(`local-chats.json` has no account key), so their settings are too. Stored in
`<state_dir>/local-settings.json` (0600) as only what was chosen; a field
never chosen (or set back to null) follows the default, so the defaults can
change with the server.

**Defaults follow whose server it is.** A person's own server and a hosted
one (South Pen Labs') answer the same three defaults: `settings`, `server`,
apply on. They differ in names: a hosted server keeps no names of Local chats.
The server had no "hosted" signal, so `MEDIA_HOSTED` (default off) says it;
`MEDIA_LOCAL_CHAT_NAMES`, when set, still decides names either way
(`local_chats.names_on`).
"""

from __future__ import annotations

import os
import time

from . import auth
from ._jsonmap import JsonMap

STORE = JsonMap("local-settings.json", mode=0o600)
KEY = "server"

LEAVES_PHONE = ("settings", "encrypted", "full")
BACKUP = ("server", "drive", "off")
#: The steps of the dial built so far; the others are listed, not choosable.
AVAILABLE_LEAVES = frozenset({"settings"})
NOT_BUILT = "not built yet"
FIELDS = frozenset({"leaves_phone", "backup", "apply_to_networks", "network_exceptions"})


def hosted() -> bool:
    """Whether South Pen Labs hosts this server (`MEDIA_HOSTED`, default off)."""
    v = (os.environ.get("MEDIA_HOSTED") or "").strip().lower()
    return v in ("1", "on", "true", "yes")


def defaults() -> dict:
    from . import local_chats

    return {"leaves_phone": "settings", "backup": "server", "apply_to_networks": True,
            "network_exceptions": {}, "names": local_chats.names_on()}


def _choosable() -> dict:
    from .local_chats import NETWORKS

    return {"leaves_phone": [{"id": k, "available": k in AVAILABLE_LEAVES,
                              **({} if k in AVAILABLE_LEAVES else {"why": NOT_BUILT})}
                             for k in LEAVES_PHONE],
            "backup": [{"id": k, "available": True} for k in BACKUP],
            "networks": [{"id": k, "name": v} for k, v in NETWORKS.items()]}


def _held() -> dict:
    got = STORE.get(KEY)
    return got if isinstance(got, dict) else {}


def view() -> dict:
    """The settings as answered: each chosen value or its default."""
    held, dflt = _held(), defaults()
    exc = held.get("network_exceptions")
    out = {k: held.get(k, dflt[k]) for k in ("leaves_phone", "backup", "apply_to_networks")}
    out["network_exceptions"] = dict(exc) if isinstance(exc, dict) else {}
    out.update({"defaults": dflt, "hosted": hosted(), "choosable": _choosable(),
                "updated": held.get("updated"), "by": held.get("by")})
    return out


def _may(bearer: str) -> tuple[bool, dict]:
    user, err = auth.gate(bearer)
    if not user:
        return False, err
    if auth.is_guest(bearer):
        return False, {"code": "guest", "status": 403,
                       "error": "a guest does not see or change the phone's chats"}
    return True, {}


def get(bearer: str) -> tuple[bool, dict]:
    """`GET /local/settings`: paired, not a guest."""
    ok, err = _may(bearer)
    return (True, view()) if ok else (False, err)


def _bad(error: str, **extra) -> tuple[bool, dict]:
    return False, {"error": error, "status": 400, **extra}


def post(body, bearer: str) -> tuple[bool, dict]:
    """`POST /local/settings {…any of the fields}`: a partial change; null
    sets a field back to its default. Answers the GET shape."""
    from .local_chats import NETWORKS

    ok, err = _may(bearer)
    if not ok:
        return False, err
    if not isinstance(body, dict):
        return _bad("the body must be an object")
    extra = sorted(set(body) - FIELDS)
    if extra:
        return _bad(f"not a setting: {extra[0]}", code="field_not_accepted", field=extra[0])
    change: dict = {}
    if "leaves_phone" in body:
        v = body["leaves_phone"]
        if v is not None and v not in LEAVES_PHONE:
            return _bad(f"leaves_phone is one of {', '.join(LEAVES_PHONE)}")
        if v is not None and v not in AVAILABLE_LEAVES:
            return _bad(f"{v}: {NOT_BUILT}", code="not_available", field="leaves_phone")
        change["leaves_phone"] = v
    if "backup" in body:
        v = body["backup"]
        if v is not None and v not in BACKUP:
            return _bad(f"backup is one of {', '.join(BACKUP)}")
        change["backup"] = v
    if "apply_to_networks" in body:
        v = body["apply_to_networks"]
        if v is not None and not isinstance(v, bool):
            return _bad("apply_to_networks is true or false")
        change["apply_to_networks"] = v
    exc = None
    if "network_exceptions" in body:
        exc = body["network_exceptions"]
        if exc is not None and not isinstance(exc, dict):
            return _bad("network_exceptions is {network: true|false|null}")
        for k, v in (exc or {}).items():
            if k not in NETWORKS:
                return _bad(f"not a network: {k}", code="unknown_network", field=k)
            if v is not None and not isinstance(v, bool):
                return _bad(f"network_exceptions.{k} is true, false or null")
    device = auth.device_id(bearer) or None

    def fn(rows: dict) -> bool:
        rec = rows.get(KEY) if isinstance(rows.get(KEY), dict) else {}
        for k, v in change.items():
            if v is None:
                rec.pop(k, None)
            else:
                rec[k] = v
        if "network_exceptions" in body:
            if exc is None:
                rec.pop("network_exceptions", None)
            else:
                # Merged by network: true/false sets that network's own
                # choice, null drops it back to the main switch.
                cur = dict(rec.get("network_exceptions") or {})
                for k, v in exc.items():
                    if v is None:
                        cur.pop(k, None)
                    else:
                        cur[k] = v
                if cur:
                    rec["network_exceptions"] = cur
                else:
                    rec.pop("network_exceptions", None)
        rec["updated"] = round(time.time(), 3)
        rec["by"] = device
        rows[KEY] = rec
        return True
    try:
        STORE.update(fn)
    except OSError as e:
        return False, {"error": f"could not save the settings ({e.strerror or 'write failed'})",
                       "status": 500}
    return True, view()
