"""A device asking to be paired, approved from a phone (server-contract.md
§9, "Pairing from the new device"; David, 16 Nov 2026, with a TV's Sasonica
refused: "Can we make it easier from the TV?.. maybe with a QR code to scan
from the phone or something?").

The ordinary way round, a device that may enrol mints a code and the new
device types it in (`POST /devices/code`, then `POST /pair`), is hard on a
TV: no camera and a remote to type with. This is the reverse:

- the new device asks, with no credential (`POST /pair/request {"name",
  "platform"?}`), and gets an id, a secret, a short code to read out and the
  link to put in a QR;
- a device that may enrol (the same gate as minting a code) looks the
  request up by id (the QR) or code (typed), sees who is asking, and allows
  it with a role, or denies it;
- the new device polls `POST /pair/wait {"id", "secret"}`; once allowed, that
  call makes the device and answers what `POST /pair` would have, once.

No device row and no token exist until that poll: a request allowed but never
collected leaves nothing behind. What is kept, in
`<state_dir>/pair-requests.json` (mode 600, like devices.json): the requests,
each with the sha256 of its secret, never the secret.

Limits: a request lapses after `TTL_S` unanswered; at most `MAX_OPEN` wait at
once and `MAX_OPEN_PER_IP` from one address (more is `too_many_requests`); a
wrong secret counts against the address like a wrong pairing code. Nothing is
alerted on: anyone can ask, so the owner hears of a request only by scanning
it or typing its code. `MEDIA_PAIR_REQUESTS=0` turns asking off.
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

TTL_S = 600
#: How long an answered or lapsed request is kept, so the asker's next poll
#: still hears "denied" or "lapsed" rather than "no such request".
KEEP_S = 3600
MAX_OPEN = 20
MAX_OPEN_PER_IP = 3
#: The short code's letters: no 0/O, 1/I/L, so it reads off a TV unambiguously.
CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
DEFAULT_ROLE = "member"

_LOCK = threading.Lock()


def enabled() -> bool:
    return (os.environ.get("MEDIA_PAIR_REQUESTS") or "1").strip().lower() not in ("0", "off", "no", "false")


def path() -> Path:
    return devices._dir() / "pair-requests.json"


def _load() -> list:
    data = devices._read(path(), {})
    reqs = data.get("requests") if isinstance(data, dict) else None
    return reqs if isinstance(reqs, list) else []


def _save(reqs: list) -> None:
    devices._write(path(), {"requests": reqs})


@contextmanager
def _txn():
    with _LOCK, devices._flock("pair-requests.lock"):
        yield


def _hash(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


def norm_code(code: str) -> str:
    """`k7q 2mx`, `K7Q-2MX` → `K7Q2MX`."""
    return "".join(c for c in (code or "").upper() if c.isalnum())


def _new_code(taken: set[str]) -> str:
    while True:
        c = "".join(secrets.choice(CODE_ALPHABET) for _ in range(6))
        if c not in taken:
            return c


def _prune(reqs: list, now: float) -> list:
    keep = []
    for r in reqs:
        if r.get("status") == "pending" and now - float(r.get("at") or 0) > TTL_S:
            r = {**r, "status": "lapsed", "decided_at": round(float(r.get("at") or 0) + TTL_S, 3)}
        if r.get("status") != "pending" and now - float(r.get("decided_at") or r.get("at") or 0) > KEEP_S:
            continue
        keep.append(r)
    return keep


def public(r: dict) -> dict:
    """What the approving phone is shown of a request."""
    code = str(r.get("code") or "")
    return {"id": r.get("id"), "name": r.get("name") or "", "platform": r.get("platform") or None,
            "code": f"{code[:3]}-{code[3:]}" if code else "", "asked_at": r.get("at"),
            "expires": round(float(r.get("at") or 0) + TTL_S, 3), "from_ip": r.get("ip") or "",
            "status": r.get("status")}


def ask(name: str, platform: str, ip: str) -> tuple[bool, dict]:
    """File a request. `(True, {"id", "secret", "code", "expires_in"})` — the
    secret is only ever in this answer — or `(False, {"code", "error"})`
    when too many wait."""
    now = time.time()
    name = " ".join((name or "").split())[:80] or "A device"
    platform = " ".join((platform or "").split())[:40]
    with _txn():
        reqs = _prune(_load(), now)
        open_ = [r for r in reqs if r.get("status") == "pending"]
        if len(open_) >= MAX_OPEN or sum(r.get("ip") == ip for r in open_) >= MAX_OPEN_PER_IP:
            _save(reqs)
            return False, {"code": "too_many_requests",
                           "error": "too many devices are waiting to pair here; try again in a few minutes"}
        secret = secrets.token_urlsafe(24)
        code = _new_code({str(r.get("code") or "") for r in open_})
        req = {"id": secrets.token_hex(6), "secret": _hash(secret), "code": code, "name": name,
               "platform": platform, "ip": ip or "", "at": round(now, 3), "status": "pending"}
        reqs.append(req)
        _save(reqs)
    return True, {"id": req["id"], "secret": secret, "code": f"{code[:3]}-{code[3:]}",
                  "expires_in": TTL_S}


def find(rid: str = "", code: str = "") -> dict | None:
    """A request by id or short code, pending or lately answered."""
    rid, code = (rid or "").strip(), norm_code(code)
    if not (rid or code):
        return None
    with _txn():
        reqs = _prune(_load(), time.time())
    for r in reqs:
        if (rid and hmac.compare_digest(str(r.get("id") or ""), rid)) or \
                (code and r.get("status") == "pending" and hmac.compare_digest(str(r.get("code") or ""), code)):
            return public(r)
    return None


def pending() -> list[dict]:
    """Every request still waiting, oldest first (Settings → Devices)."""
    with _txn():
        reqs = _prune(_load(), time.time())
    return [public(r) for r in reqs if r.get("status") == "pending"]


def decide(rid: str, *, allow: bool, role: str = "", name: str = "", by: str = "") -> tuple[str, dict]:
    """The phone's answer. `("ok", request)`, or `("unknown"|"answered"|"lapsed",
    {})`. `role` is admin, member or guest (default member); `name` is what
    the device will be called (default the name it asked with)."""
    rid = (rid or "").strip()
    now = time.time()
    with _txn():
        reqs = _prune(_load(), now)
        req = next((r for r in reqs if rid and r.get("id") == rid), None)
        if req is None:
            _save(reqs)
            return "unknown", {}
        if req.get("status") != "pending":
            _save(reqs)
            return ("lapsed" if req.get("status") == "lapsed" else "answered"), {}
        req["status"] = "approved" if allow else "denied"
        req["decided_at"] = round(now, 3)
        req["by"] = by or ""
        if allow:
            req["role"] = role or DEFAULT_ROLE
            given = " ".join((name or "").split())[:80]
            if given:
                req["name"] = given
        _save(reqs)
    return "ok", public(req)


def wait(rid: str, secret: str, ip: str = "") -> tuple[str, dict]:
    """The asking device's poll. `("pending"|"denied"|"lapsed"|"unknown", {})`,
    or `("approved", <devices.redeem's answer>)` exactly once: the device is
    made by the poll that carries its token, and the request is spent."""
    with _txn():
        reqs = _prune(_load(), time.time())
        req = next((r for r in reqs if rid and r.get("id") == rid), None)
        if req is None or not req.get("secret") or \
                not hmac.compare_digest(str(req.get("secret") or ""), _hash(secret or "")):
            _save(reqs)
            return "unknown", {}
        status = str(req.get("status") or "")
        if status != "approved":
            _save(reqs)
            return (status if status in ("pending", "denied", "lapsed") else "unknown"), {}
        # Spend the request before the device is written, as redeem burns its
        # code first: a failure after this leaves no second device to collect.
        req["status"] = "spent"
        req["secret"] = ""
        _save(reqs)
    # The ordinary pairing code, minted and redeemed at once, so the device
    # row is made exactly as `POST /pair` makes it (an admin is never given
    # the enrol bit this way: managing devices is granted at the desk).
    code, _ = devices.mint_code(req.get("name") or "device", role=req.get("role") or DEFAULT_ROLE)
    got = devices.redeem(code, req.get("name") or "device", ip)
    if not got:
        return "unknown", {}
    return "approved", got
