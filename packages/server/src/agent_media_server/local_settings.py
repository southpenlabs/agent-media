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
#: `full` is built (18 Nov 2026, local_messages.py), but on a hosted server
#: only with MEDIA_LOCAL_FULL_ON_HOSTED=1 (`full_available`).
AVAILABLE_LEAVES = frozenset({"settings", "full"})
NOT_BUILT = "not built yet"
HOSTED_FULL = "not on a server South Pen Labs hosts"
FIELDS = frozenset({"leaves_phone", "backup", "apply_to_networks", "network_exceptions"})


def hosted() -> bool:
    """Whether South Pen Labs hosts this server (`MEDIA_HOSTED`, default off)."""
    v = (os.environ.get("MEDIA_HOSTED") or "").strip().lower()
    return v in ("1", "on", "true", "yes")


def full_available() -> bool:
    """Whether `full` may be chosen: on a person's own server, yes; on one
    South Pen Labs hosts, only with `MEDIA_LOCAL_FULL_ON_HOSTED=1` (other
    people's messages, readable, on SPL's machines)."""
    if not hosted():
        return True
    v = (os.environ.get("MEDIA_LOCAL_FULL_ON_HOSTED") or "").strip().lower()
    return v in ("1", "on", "true", "yes")


def _available(step: str) -> tuple[bool, str | None]:
    if step not in AVAILABLE_LEAVES:
        return False, NOT_BUILT
    if step == "full" and not full_available():
        return False, HOSTED_FULL
    return True, None


def _why(step: str) -> str:
    """What each step of the dial sends, and who can read it, in plain words."""
    who = ("South Pen Labs' machines, which host this server" if hosted()
           else "this server, which is yours, and the agents that run on it")
    if step == "settings":
        return ("Only a list of your chats (each one's network and id"
                + ("" if not hosted() else ", no names") + ") and what you set on them in "
                "Sasonica, like archive and quiet. Your messages stay on the phone, so other "
                "devices see the settings but not the words.")
    if step == "encrypted":
        return ("Your messages too, encrypted so only your own devices can read them: "
                "the desk and a tablet show them, but the server, its agents and search "
                "cannot read them.")
    return ("Your messages too, readable by " + who + ". Agents can read and answer these "
            "chats, search finds them, the desk and other devices show them, and a lost "
            "phone loses nothing. Photos and files stay on the phone. Switching back "
            "deletes every copied message from the server.")


def defaults() -> dict:
    from . import local_chats

    return {"leaves_phone": "settings", "backup": "server", "apply_to_networks": True,
            "network_exceptions": {}, "names": local_chats.names_on()}


def _choosable() -> dict:
    from .local_chats import NETWORKS

    steps = []
    for k in LEAVES_PHONE:
        ok, why_not = _available(k)
        steps.append({"id": k, "available": ok, "explain": _why(k),
                      **({} if ok else {"why": why_not})})
    return {"leaves_phone": steps,
            "backup": [{"id": k, "available": True} for k in BACKUP],
            "networks": [{"id": k, "name": v} for k, v in NETWORKS.items()]}


def _held() -> dict:
    got = STORE.get(KEY)
    return got if isinstance(got, dict) else {}


#: The owner's Matrix id, read once per this long (`matrix_user`).
MATRIX_USER_TTL_S = 3600.0
_MATRIX_USER: list = [None, 0.0]


def matrix_user() -> str | None:
    """The Matrix account the phone signs into for the encrypted copy (step
    6: the user's own account, the companion a device of it): the owner's,
    from `MEDIA_LOCAL_MATRIX_USER`, else asked of the homeserver with the
    owner's token (`whoami`), kept an hour. None when this server has no
    owner's Matrix account. Only the id: the phone finds the homeserver from
    the id's server (.well-known) and signs in with the password itself."""
    given = (os.environ.get("MEDIA_LOCAL_MATRIX_USER") or "").strip()
    if given:
        return given
    if _MATRIX_USER[0] is not None and time.monotonic() - _MATRIX_USER[1] < MATRIX_USER_TTL_S:
        return _MATRIX_USER[0] or None
    from . import matrix

    token = matrix.owner_token()
    if not token:
        return None
    import json
    import urllib.request

    homeserver = (os.environ.get("MATRIX_HOMESERVER") or "").rstrip("/")
    if not homeserver:
        from agent_media_core import matrix as core_matrix

        homeserver = core_matrix.DEFAULT_HOMESERVER.rstrip("/")
    try:
        req = urllib.request.Request(f"{homeserver}/_matrix/client/v3/account/whoami",
                                     headers={"Authorization": f"Bearer {token}"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            user = str(json.loads(resp.read()).get("user_id") or "")
    except Exception:  # noqa: BLE001 - the settings answer without it
        return None
    _MATRIX_USER[0], _MATRIX_USER[1] = user, time.monotonic()
    return user or None


def view() -> dict:
    """The settings as answered: each chosen value or its default."""
    held, dflt = _held(), defaults()
    exc = held.get("network_exceptions")
    out = {k: held.get(k, dflt[k]) for k in ("leaves_phone", "backup", "apply_to_networks")}
    out["network_exceptions"] = dict(exc) if isinstance(exc, dict) else {}
    out.update({"defaults": dflt, "hosted": hosted(), "choosable": _choosable(),
                "updated": held.get("updated"), "by": held.get("by")})
    return out


def _matrix_view(bearer: str) -> dict:
    """`matrix` in GET /local/settings, for the owner's devices only: the
    account the encrypted copy signs the phone into."""
    user, _err = auth.may_admin(bearer)
    if not user:
        return {}
    return {"matrix": {"user_id": matrix_user()}}


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
    return (True, {**view(), **_matrix_view(bearer)}) if ok else (False, err)


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
        if v is not None and not _available(v)[0]:
            return _bad(f"{v}: {_available(v)[1]}", code="not_available", field="leaves_phone")
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
    was_full = view()["leaves_phone"] == "full"

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
    out = view()
    if was_full and out["leaves_phone"] != "full":
        # Off Full copy: every Local message kept here goes, and its rows in
        # the search index (§6.61 Full copy).
        from . import local_messages

        out["purged"] = local_messages.purge()
    return True, out
