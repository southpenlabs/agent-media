"""Who may come into this server and with what role: the server's own list
(docs/proposals/2026-11-18-account-access-and-tiers.md; David, 18 Nov 2026:
"Yes, the hybrid").

The server keeps the list and has the final say, so it works with
sasonica.com down, a removal takes effect at once, and the list moves with
the server. sasonica.com shows it and an owner (or a reseller across their
servers) may add or remove people there: those changes come back in the
server's report answer (`servers_report`) and are merged here, the newest
change winning either way.

A person is kept by email (an account's verified email, so one can be let
in before they have ever signed in here), with `role` (admin, member,
guest), `services` (for a reseller's server, which services this person may
use; None = whatever their tier allows), `changed_at` (unix s), `by`
("server" or "web") and `removed` (kept as a tombstone, so a removal made
here isn't undone by an older add from the web).

Settings → People: `GET /settings/people` and `POST /settings/people
{"email", "role"?, "services"?, "remove"?}`, an admin. Stored in
`<state_dir>/grants.json` (0600).
"""

from __future__ import annotations

import re
import time

from ._jsonmap import JsonMap

STORE = JsonMap("grants.json", mode=0o600)

_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _norm(email: str) -> str:
    e = (email or "").strip().lower()
    return e if _EMAIL.match(e) else ""


def people(include_removed: bool = False) -> list[dict]:
    rows = [{"email": k, **v} for k, v in STORE.all().items() if isinstance(v, dict)]
    rows = [r for r in rows if include_removed or not r.get("removed")]
    return sorted(rows, key=lambda r: r["email"])


def role_for(claims: dict) -> str:
    """The role this account was granted here, or "": by its verified email."""
    if claims.get("email_verified") is False:
        return ""
    got = STORE.get(_norm(str(claims.get("email") or "")))
    if not isinstance(got, dict) or got.get("removed"):
        return ""
    from . import devices

    role = str(got.get("role") or "")
    return role if role in devices.ROLES else ""


def services_for(email: str) -> list[str] | None:
    """The services the owner set for this person (a reseller's server), or
    None when none were set."""
    got = STORE.get(_norm(email))
    if not isinstance(got, dict) or got.get("removed"):
        return None
    s = got.get("services")
    return [str(x) for x in s] if isinstance(s, list) else None


#: `put`'s services left as they are (None clears them).
KEEP = object()


def put(email: str, *, role: str | None = None, services=KEEP, remove: bool = False,
        by: str = "server", changed_at: float | None = None) -> bool:
    """Grant, change or remove one person. A change older than the one kept
    is ignored (the newest wins). Whether it was applied."""
    from . import devices

    key = _norm(email)
    if not key:
        return False
    at = float(changed_at if changed_at is not None else time.time())
    applied = []

    def change(d: dict) -> bool:
        old = d.get(key) if isinstance(d.get(key), dict) else None
        if old and float(old.get("changed_at") or 0) > at:
            return False
        row = dict(old or {})
        if remove:
            row["removed"] = True
        else:
            row.pop("removed", None)
            r = role if role is not None else row.get("role") or "member"
            row["role"] = r if r in devices.ROLES else "member"
            if services is not KEEP:
                row["services"] = [str(x) for x in services] if isinstance(services, list) else None
        row.update(changed_at=round(at, 3), by=by)
        d[key] = row
        applied.append(True)
        return True

    STORE.update(change)
    return bool(applied)


def merge_from_web(rows) -> int:
    """Changes made on sasonica.com (the report's answer, `people`): each
    `{email, role?, services?, removed?, changed_at}` applied when newer."""
    n = 0
    for r in rows or []:
        if not isinstance(r, dict) or not r.get("changed_at"):
            continue
        try:
            at = float(r["changed_at"])
        except (TypeError, ValueError):
            continue
        if put(str(r.get("email") or ""), role=r.get("role"),
               services=r["services"] if "services" in r else KEEP,
               remove=bool(r.get("removed")), by="web", changed_at=at):
            n += 1
    return n


def for_report() -> list[dict]:
    """The list as sasonica.com is told it, removals included."""
    return [{k: r[k] for k in ("email", "role", "services", "changed_at", "by", "removed")
             if k in r} for r in people(include_removed=True)]


def settings(bearer: str, body: dict | None = None) -> tuple[bool, dict]:
    """`GET`/`POST /settings/people` (an admin)."""
    from . import auth, devices

    user, err = auth.may_admin(bearer)
    if not user:
        return False, err
    if body is not None:
        email = _norm(str(body.get("email") or ""))
        if not email:
            return False, {"status": 400, "code": "email", "error": "an email address, please"}
        role = body.get("role")
        if role is not None and role not in devices.ROLES:
            return False, {"status": 400, "code": "role",
                           "error": f"role is one of {', '.join(devices.ROLES)}"}
        services = body["services"] if "services" in body else KEEP
        if services is not KEEP and services is not None and not isinstance(services, list):
            return False, {"status": 400, "code": "services", "error": "services is a list or null"}
        put(email, role=role, services=services, remove=bool(body.get("remove")))
    return True, {"people": people()}
