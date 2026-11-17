"""Which server serves each part of the app: music, maps, watch, books, the
Organiser and People (docs/proposals/2026-11-18-services-per-server.md).

David, 18 Nov 2026, on the layering: the account's membership tier says
which services it may use and from which servers (its ceiling; built with
the account-roles work, until then every service is allowed); **the home
server sets the default server for each service** (this module, Settings →
Services); a device may override a service for itself, within what the tier
allows (the app keeps that).

`GET /settings/services` (any paired device) answers every service: its
`routes` (the path prefixes the app sends there), the `server` chosen for it
(null = this server), whether it is `allowed`, and the `choices`: this
server and the servers it has peer links with. `POST /settings/services
{"service", "server": {"base", "server_id"?, "name"?} | null}` sets one (an
admin); null puts it back on this server. Kept in `<state_dir>/services.json`.
"""

from __future__ import annotations

import os
import socket
import urllib.parse

from ._jsonmap import JsonMap

STORE = JsonMap("services.json")

#: Each service and the route prefixes it covers, in the order Settings
#: lists them. A prefix matches a path that is it, or it followed by "/" or "?".
SERVICES: dict[str, dict] = {
    "music": {"label": "Music, podcasts and rooms", "routes": ["/music", "/podcasts", "/rooms"]},
    "maps": {"label": "Maps", "routes": ["/map"]},
    "watch": {"label": "Movies and shows", "routes": ["/watch", "/accounts/trakt"]},
    "books": {"label": "Books", "routes": ["/books", "/book"]},
    "organiser": {"label": "Organiser", "routes": ["/org", "/calendar", "/notes"]},
    "people": {"label": "People", "routes": ["/contacts"]},
}


def this_server() -> dict:
    from . import server_address, servers_report

    return {"base": server_address.current(), "server_id": servers_report.server_id(),
            "name": os.environ.get("MEDIA_SERVER_NAME", "").strip() or socket.gethostname()}


def _choices() -> list[dict]:
    """This server, then every server this one has a peer link with."""
    out = [{**this_server(), "this": True}]
    try:
        from agent_media_core import peer_link

        for row in peer_link.links():
            got = peer_link.load(row["alias"]) if row.get("paired") else None
            if got and got.get("url"):
                out.append({"base": got["url"], "server_id": got.get("server_id") or "",
                            "name": got.get("server_name") or row["alias"]})
    except Exception:  # noqa: BLE001 - no links: this server only
        pass
    return out


def _allowed(service: str, user: dict | None) -> bool:
    """The account's tier allows this service. Every one until membership
    tiers are built (the account-roles item of the threads-across-machines
    plan)."""
    return True


def chosen(service: str) -> dict | None:
    got = STORE.get(service)
    return dict(got) if isinstance(got, dict) and got.get("base") else None


def report(user: dict | None = None) -> dict:
    return {"this": this_server(), "choices": _choices(),
            "services": [{"id": sid, "label": s["label"], "routes": list(s["routes"]),
                          "server": chosen(sid), "allowed": _allowed(sid, user)}
                         for sid, s in SERVICES.items()]}


def _clean_base(base: str) -> str:
    u = urllib.parse.urlsplit((base or "").strip())
    if u.scheme not in ("https", "http") or not u.netloc or u.username or u.password:
        return ""
    return f"{u.scheme}://{u.netloc}"


def settings(bearer: str, body: dict | None = None) -> tuple[bool, dict]:
    """`GET /settings/services` (any paired device), `POST` an admin."""
    from . import auth

    if body is None:
        user, err = auth.gate(bearer)
        if not user:
            return False, err
        return True, report(user)
    user, err = auth.may_admin(bearer)
    if not user:
        return False, err
    sid = str(body.get("service") or "")
    if sid not in SERVICES:
        return False, {"status": 400, "code": "service",
                       "error": f"service is one of {', '.join(SERVICES)}"}
    server = body.get("server")
    if server is not None and not isinstance(server, dict):
        return False, {"status": 400, "code": "server", "error": "server is an object or null"}
    value = None
    if server:
        base = _clean_base(str(server.get("base") or ""))
        if not base:
            return False, {"status": 400, "code": "server", "error": "server.base is an address"}
        if base == _clean_base(this_server()["base"]):
            value = None                      # this server: the default
        else:
            value = {"base": base, "server_id": str(server.get("server_id") or "")[:64],
                     "name": " ".join(str(server.get("name") or "").split())[:80]}

    def change(d: dict) -> bool:
        if value is None:
            return d.pop(sid, None) is not None
        d[sid] = value
        return True

    STORE.update(change)
    return True, report(user)
