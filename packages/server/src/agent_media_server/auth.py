"""The gate in front of every app route: a paired device.

server-contract.md §9: the gate is a known device token. It used to be "a
known device token, **or** an Audiobookshelf bearer that passes §4.1"; the
ABS fallback (and `auth_abs.py`) went when Audiobookshelf was left on
11 Nov 2026 (David: "I'd like to move away from audiobookshelf" — "All the
way"). This module is the one place the gate is said:

  gate(bearer)                → (user, {}) or (None, error)   — may manage sessions
  may_control_speech(bearer)  → (ok, error)                    — the speech bar, harnesses
  identity(bearer)            → (user, status)                 — who, without the may-reply check
  may_reply(user)             → (ok, why)
  may_admin(bearer)           → (user, {}) or (None, error)   — host setup (project roots); admins only

The check is local and cheap: a hash and a compare against `devices.json`,
never a network call. A bearer that is not a known device token — revoked,
mistyped, or nobody's — is 401, which the app answers by pairing again.

A device is the owner, less what its role takes away (a guest may read and
listen, not type). v1 has one scope; see §9.
"""

from __future__ import annotations

import threading

import hashlib
import re

from . import devices

# The source address of the request this thread is answering, for a device's
# `last_ip`. Set by `app.dispatch` before any route runs; a handler thread
# serves one request at a time, so a thread-local is exactly per-request.
_REQ = threading.local()


def set_client_ip(ip: str) -> None:
    _REQ.ip = ip or ""


def client_ip() -> str:
    """The source address of the request this thread is answering ("" when
    none was set)."""
    return getattr(_REQ, "ip", "") or ""


def set_request_bearer(bearer: str) -> None:
    """The bearer of the request this thread is answering, for
    `request_account` — set by `app.dispatch` beside the address."""
    _REQ.bearer = bearer or ""
    _REQ.account = None


def _device(bearer: str) -> dict | None:
    """The device this bearer is — never a jobs-only one (`pair --jobs`):
    another host's worker token takes jobs down its stream and is no way in
    to anything else (it is refused like an unknown token)."""
    dev = devices.lookup(bearer, getattr(_REQ, "ip", ""))
    return None if devices.is_jobs_worker(dev) else dev


def jobs_device_id(bearer: str) -> str:
    """The id of any paired device, jobs-only ones included: what the jobs
    worker routes (`/jobs/events`, `/jobs/result`) ask, and then check
    against the devices that may take jobs (phone_jobs.jobs_devices)."""
    dev = devices.lookup(bearer, getattr(_REQ, "ip", ""))
    return str(dev.get("id") or "") if dev else ""


def _owner(dev: dict) -> dict:
    """The user a device token stands for: the owner, marked as a device so
    `may_reply` can tell.

    `enrol` is the one right a device does not automatically have (§9): it
    rides here so a route can ask without reading the store again, as does
    `role` (admin, member or guest; docs/proposals/2026-10-18-roles.md).
    """
    return {"username": f"device:{dev.get('name') or dev.get('id')}", "type": "root",
            "device": dev.get("id"), "enrol": devices.may_enrol(dev), "role": devices.role_of(dev),
            "account_key": device_account(dev)}


# --- whose book (contacts, the timeline) ------------------------------------------
#
# One address book per person, several per server (contacts proposal, "Where
# contacts live"). The owner's is "" — today's paths, unchanged; any other
# account's is a key safe as a directory name. An account's identity is the
# `iss`/`sub` its enrolment wrote on the device row.

#: An account key: "" (the owner) or this.
ACCOUNT_KEY = re.compile(r"acct-[0-9a-f]{20}")
NOBODY = "nobody"


def _key(prefix: str, ident: str) -> str:
    return f"{prefix}-" + hashlib.sha256(ident.encode()).hexdigest()[:20]


def account_key_of(iss: str, sub: str) -> str:
    return _key("acct", f"{(iss or '').rstrip('/')}|{sub}")


def device_account(dev: dict) -> str:
    """The book a device reads: "" (the owner's) for a code-paired device
    (someone at the desk paired it) and for the owner's account; the
    account's own key for anyone else's.

    The owner's account is `MEDIA_OWNER_ACCOUNT` (MEDIA_OIDC_ALLOW's form:
    `<iss>|<sub>`, `<iss>|email=<addr>`). Unset, an account whose device may
    enrol others is the owner's — that is an admin, who could read the store
    from a shell anyway, and it is how David's own enrolled devices keep his
    book with nothing to set."""
    sub = str(dev.get("sub") or "")
    if not sub:
        return ""
    iss = str(dev.get("iss") or "").rstrip("/")
    from . import oidc

    rules = oidc._list("MEDIA_OWNER_ACCOUNT")
    if rules:
        claims = {"iss": iss, "sub": sub, "email": str(dev.get("account") or "")}
        return "" if oidc._matches(claims, rules) else account_key_of(iss, sub)
    return "" if devices.may_enrol(dev) else account_key_of(iss, sub)


def account_key(user: dict | None) -> str:
    """The book this caller (a `gate`/`identity` user) reads."""
    if not user or not user.get("device"):
        return NOBODY
    return str(user.get("account_key") or "")


def request_account() -> str:
    """The book of the request this thread is answering ("" — the owner's —
    outside one: the background sync, the CLI). A request nobody signed is
    nobody's book, which holds nothing."""
    bearer = getattr(_REQ, "bearer", None)
    if bearer is None:
        return ""
    got = getattr(_REQ, "account", None)
    if got is None:
        # Once per request: /targets asks for every room.
        got = account_key(identity(bearer)[0]) if bearer else NOBODY
        _REQ.account = got
    return got


def identity(bearer: str) -> tuple[dict | None, int]:
    """`(user, status)`: the device's owner and 200, else `(None, 401)`."""
    dev = _device(bearer)
    if dev:
        return _owner(dev), 200
    return None, 401


def _unknown() -> dict:
    return {"error": "not a paired device (unknown or revoked token)", "status": 401}


def may_reply(user: dict | None) -> tuple[bool, str]:
    if user and user.get("device"):
        if user.get("role") == "guest":
            return False, "a guest may read and listen, not type"
        return True, str(user.get("username") or "")
    return False, "not a paired device"


def gate(bearer: str) -> tuple[dict | None, dict]:
    """`(user, {})` when this bearer may manage sessions, else `(None, error)`
    with the error's `status` set (401)."""
    dev = _device(bearer)
    if dev:
        return _owner(dev), {}
    return None, _unknown()


def may_admin(bearer: str) -> tuple[dict | None, dict]:
    """`(user, {})` when this bearer may change how the host is set up — the
    folders new projects go in (project_roots.py) — else `(None, error)`.

    `gate`, and then the role: a member or guest device is 403 `not_admin`
    (docs/proposals/2026-10-18-roles.md). A device without a role is an admin.
    """
    user, err = gate(bearer)
    if not user:
        return None, err
    if user.get("device") and user.get("role", "admin") != "admin":
        return None, {"status": 403, "code": "not_admin",
                      "error": "only an admin may change how this server is set up"}
    return user, {}


def may_control_speech(bearer: str) -> tuple[bool, dict]:
    """The speech bar's and the harness routes' gate: the same person who may
    reply may pause, or install an agent."""
    if _device(bearer):
        return True, {}
    return False, _unknown()


def is_guest(bearer: str) -> bool:
    """Whether this bearer is a guest device (it may read and listen only)."""
    dev = _device(bearer)
    return bool(dev) and devices.role_of(dev) == "guest"


def is_device(bearer: str) -> bool:
    return _device(bearer) is not None


def device_id(bearer: str) -> str:
    """The id of the device this bearer is, "" for anything else — the `mic`
    frame leaves out a device's own asks (§6.20)."""
    dev = _device(bearer)
    return str(dev.get("id") or "") if dev else ""


def may_enrol(bearer: str) -> tuple[dict | None, dict]:
    """`gate`, and then the enrol bit: `(user, {})` for a device that may pair
    another, else `(None, error)` with a status.

    The bit is carried by a device row, and the person at the desk has the
    CLI — so a device without it is 403. A 401 for a bearer that is nobody, so
    the app knows to pair rather than to ask for a different device.
    """
    user, err = gate(bearer)
    if not user:
        return None, err
    if not user.get("enrol"):
        return None, {"status": 403, "code": "not_enrolled",
                      "error": "this device may not manage other devices"}
    return user, {}
