"""Asking to join a server with a Sasonica account (server-contract.md §9,
"Asking to join"; David, 16 Nov 2026: a new account asks, the owner approves).

Anyone can make a Sasonica account (cms.sasonica.com takes sign-ups). Before
this, an account the server's config did not name (MEDIA_OIDC_ALLOW) was
turned away at `POST /enrol` with nothing for the owner to see. Now its
sign-in files a request:

- the owner gets an alert, `join.<id>` at `needs`, with Allow, Allow as
  guest and Deny (`POST /alerts/action`, which runs `act` here);
- the asking app gets the request's id and a secret, and polls
  `POST /enrol/wait` with them; once allowed, that call answers what
  `POST /enrol` would have (a device token), once;
- an allowed account is remembered here (`allowed_role`), so its next sign-in
  on another device goes straight through, with the role it was given;
  `devices --revoke-account` forgets it again.

What is kept, in `<state_dir>/join-requests.json` (mode 600, like
devices.json): the requests, each with the claims it was asked with, the
sha256 of its secret and, until it is spent, the sign-in's refresh token;
and the allowed accounts, `{"<iss>|<sub>": {"role", "email", "at"}}`.

Limits: one open request per account (asking again refreshes it and does not
alert twice), at most `MAX_OPEN` open at once (more is `too_many_requests`), a
request lapses after `TTL_S`, and a denied account is not alerted on again for
`DENY_QUIET_S`. The first device signed in after acceptance carries a welcome
(`take_welcome`; MEDIA_JOIN_WELCOME replaces its text).
`MEDIA_OIDC_REQUESTS=0` turns asking off (a stranger is
`not_enrolled` with no request, as before).
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import threading
import time
from contextlib import contextmanager
from pathlib import Path

from . import devices

TTL_S = 7 * 86400
DENY_QUIET_S = 30 * 86400
MAX_OPEN = 20
#: The alert's buttons: id → (role given, label). Deny gives none.
ACTIONS = {"allow": ("member", "Allow"), "guest": ("guest", "Allow as guest"),
           "deny": ("", "Deny")}

_LOCK = threading.Lock()


def enabled() -> bool:
    return (os.environ.get("MEDIA_OIDC_REQUESTS") or "1").strip().lower() not in ("0", "off", "no", "false")


def path() -> Path:
    return devices._dir() / "join-requests.json"


def _key(claims: dict) -> str:
    return f"{str(claims.get('iss') or '').rstrip('/')}|{claims.get('sub') or ''}"


def _load() -> dict:
    data = devices._read(path(), {})
    reqs = data.get("requests")
    allowed = data.get("allowed")
    return {"requests": reqs if isinstance(reqs, list) else [],
            "allowed": allowed if isinstance(allowed, dict) else {}}


@contextmanager
def _txn():
    """The in-process lock and the file lock, for a read-modify-write."""
    with _LOCK, devices._flock("join-requests.lock"):
        yield


def _hash(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


def _prune(data: dict, now: float) -> None:
    keep = []
    for r in data["requests"]:
        if r.get("status") == "pending" and now - r.get("at", 0) > TTL_S:
            r = {**r, "status": "lapsed"}
            r.pop("rt", None)
        # Settled ones are kept a while for the record, then dropped.
        if r.get("status") != "pending" and now - r.get("decided_at", r.get("at", 0)) > DENY_QUIET_S:
            continue
        keep.append(r)
    data["requests"] = keep


def allowed_role(claims: dict) -> str:
    """The role this account was allowed with here, or "" when it was not."""
    got = _load()["allowed"].get(_key(claims))
    role = str((got or {}).get("role") or "")
    return role if role in devices.ROLES else ""


#: The welcome an account gets with its first device after it is let in
#: (David, 17 Nov 2026: "acceptance should come with an onboarding
#: message"). MEDIA_JOIN_WELCOME replaces the text; {server} and {role} are
#: filled in.
WELCOME = {
    "member": ("An administrator of {server} has approved your registration. You can read "
               "and listen to the chats here, start your own from the menu, and talk to the "
               "agents by typing or with the mic. Settings has your voice, your devices and "
               "your account."),
    "guest": ("An administrator of {server} has approved your registration as a guest. You "
              "can read the chats here and listen to them, but not type into them. Ask an "
              "administrator if you need more."),
}


def take_welcome(claims: dict, role: str, server: str) -> dict | None:
    """`{"title", "text"}` for an account let in by answering its request, the
    first time one of its devices signs in after; None after that, and for an
    account let in by the server's config."""
    key = _key(claims)
    with _txn():
        data = _load()
        got = data["allowed"].get(key)
        if not got or not got.get("welcome"):
            return None
        got["welcome"] = False
        devices._write(path(), data)
    custom = (os.environ.get("MEDIA_JOIN_WELCOME") or "").strip()
    text = custom or WELCOME.get(role) or WELCOME["member"]
    try:
        text = text.format(server=server, role=role)
    except (KeyError, IndexError, ValueError):
        pass
    return {"title": f"Welcome to {server}", "text": text}


def forget(who: str) -> int:
    """Drop the allowance of an account, by `sub` or email (`revoke_account`).
    How many went."""
    who = (who or "").strip().lower()
    if not who:
        return 0
    with _txn():
        data = _load()
        gone = [k for k, v in data["allowed"].items()
                if k.rpartition("|")[2].lower() == who or str(v.get("email") or "").lower() == who]
        for k in gone:
            del data["allowed"][k]
        if gone:
            devices._write(path(), data)
    return len(gone)


def _alert(req: dict, server: str) -> None:
    from . import alerts

    who = req.get("username") or req.get("email") or "Someone"
    email = req.get("email") or ""
    title = f"{who} asks to join {server}" + (f" ({email})" if email and email != who else "")
    detail = (f"A Sasonica account signed in on {req.get('device') or 'a device'} and asked for access "
              f"to {server}. Allow lets it in as a member (it can see and use this server, but not "
              f"manage devices); Allow as guest lets it read and listen only; Deny turns it away.")
    alerts.report({"id": f"join.{req['id']}", "level": "needs", "title": title[:200],
                   "detail": detail, "host": server,
                   "actions": [{"id": a, "label": label} for a, (_, label) in ACTIONS.items()]})


def ask(claims: dict, device: str, ip: str, *, refresh_token: str = "",
        username: str = "", picture: str = "", server: str = "") -> tuple[bool, dict]:
    """File (or refresh) this account's request. `(True, {"id", "secret",
    "status"})` — the secret is only ever in this answer — or `(False,
    {"code", "error"})`: `denied` (turned away lately), `too_many_requests`."""
    now = time.time()
    key = _key(claims)
    with _txn():
        data = _load()
        _prune(data, now)
        mine = [r for r in data["requests"] if r.get("key") == key]
        if any(r.get("status") == "denied" for r in mine):
            return False, {"code": "denied", "error": "this server's owner turned that account away"}
        open_ = next((r for r in mine if r.get("status") == "pending"), None)
        if open_ is None and sum(r.get("status") == "pending" for r in data["requests"]) >= MAX_OPEN:
            return False, {"code": "too_many_requests",
                           "error": "this server has too many requests waiting; try again later"}
        secret = secrets.token_urlsafe(24)
        fresh = open_ is None
        req = open_ or {"id": secrets.token_hex(5), "key": key, "status": "pending"}
        req.update({"at": round(now, 3), "secret": _hash(secret), "ip": ip or "",
                    "device": " ".join((device or "").split())[:80],
                    "claims": {k: claims.get(k) for k in ("iss", "sub", "email", "email_verified",
                                                          "name", "preferred_username", "picture")
                               if claims.get(k) is not None},
                    "email": str(claims.get("email") or ""), "username": username, "picture": picture})
        if refresh_token:
            req["rt"] = refresh_token
        if fresh:
            data["requests"].append(req)
        devices._write(path(), data)
    if fresh:
        _alert(req, server)
    return True, {"id": req["id"], "secret": secret, "status": "pending"}


def act(alert_id: str, action: str) -> tuple[bool, dict]:
    """The alert's button (alerts.ACTION_HANDLERS): allow, guest or deny."""
    from . import alerts

    rid = alert_id.removeprefix("join.")
    if action not in ACTIONS:
        return False, {"error": "not an answer to a join request"}
    role, label = ACTIONS[action]
    now = time.time()
    with _txn():
        data = _load()
        req = next((r for r in data["requests"] if r.get("id") == rid), None)
        if req is None or req.get("status") != "pending":
            alerts.report({"id": alert_id, "level": "ok", "title": "Join request already answered"})
            return False, {"error": "that request has already been answered or has lapsed"}
        req["status"] = "allowed" if role else "denied"
        req["decided_at"] = round(now, 3)
        if role:
            req["role"] = role
            data["allowed"][req["key"]] = {"role": role, "email": req.get("email") or "",
                                           "at": round(now, 3), "welcome": True}
        else:
            req.pop("rt", None)
        devices._write(path(), data)
    who = req.get("username") or req.get("email") or "the account"
    note = (f"{who} may join as a {role}; their app signs in by itself." if role
            else f"{who} was turned away.")
    alerts.report({"id": alert_id, "level": "ok", "title": f"Join request: {label} — {who}"[:200]})
    return True, {"message": note, "role": role or None}


def wait(rid: str, secret: str) -> tuple[str, dict]:
    """The asking app's poll. `("pending", {})`, `("denied", {})`,
    `("lapsed", {})`, `("unknown", {})` — or `("allowed", {"claims", "role",
    "device", "rt"})` exactly once: the request is spent by the answer that
    carries it, so a second poll with the same secret is `unknown`."""
    with _txn():
        data = _load()
        _prune(data, time.time())
        req = next((r for r in data["requests"] if r.get("id") == rid), None)
        if req is None or not hmac.compare_digest(str(req.get("secret") or ""), _hash(secret or "")):
            return "unknown", {}
        status = str(req.get("status") or "")
        if status == "allowed":
            out = {"claims": req.get("claims") or {}, "role": req.get("role") or "member",
                   "device": req.get("device") or "", "rt": req.get("rt") or "",
                   "username": req.get("username") or "", "picture": req.get("picture") or ""}
            req["status"] = "spent"
            req.pop("rt", None)
            req["secret"] = ""
            devices._write(path(), data)
            return "allowed", out
        if status != "pending":
            devices._write(path(), data)
            return ("denied" if status == "denied" else "lapsed" if status == "lapsed" else "unknown"), {}
        return "pending", {}

