"""This server, on the accounts that have a device here (roadmap item 20).

sasonica.com's Account page lists the servers an account can open. Nothing
else knows where a server is, so the server says: when an account signs in
here (POST /enrol) and then daily, it POSTs its id, name and addresses to the
account's issuer (`{iss}/api/servers`, the sasonica_oidc module) with that
account's own access token — the refresh token kept at sign-in. An account
whose devices are all gone stops being refreshed and drops off the list after
30 days unheard (the issuer's cron).

Addresses: `api` is where the app reaches this server — MEDIA_PUBLIC_URL,
else the running quick tunnel, else the address the last sign-in came in on.
`web` is the Sasonica web app that talks to it, if one is served:
MEDIA_WEB_APP_URL, else `<api>/app` when this server serves the bundle itself
(web_app.py; it is on the public listener too, so that holds for a tunnel
address as well as a LAN one). MEDIA_SERVERS_REPORT=0 turns the reports off.

`role` and `hosts`: the account's role here as it takes effect, and the
names of the machines paired here as jobs workers, so sasonica.com can list
the machines an account administers. The answer's `role_ceiling` (the
highest role the account may have on any server, from its permissions on
sasonica.com) is kept per account and lowers its devices' roles here
(devices.role_of; docs/proposals/2026-11-14-hosts-for-auto-mode.md).

`devices`: that account's devices here and the app build each says it runs
(POST /device/app), so sasonica.com can offer the latest build to a phone
that is behind (David, 7 Nov 2026: "see and download the latest build if
you're not already on it"). A device whose build changes reports again.
"""

from __future__ import annotations

import json
import os
import secrets
import socket
import sys
import threading
import urllib.error
import urllib.request

from agent_media_core._paths import state_dir

from . import devices

_PERIOD_S = 86400.0
_FIRST_S = 120.0
_STARTED: list = [False]


def enabled() -> bool:
    return os.environ.get("MEDIA_SERVERS_REPORT", "1") != "0"


def server_id() -> str:
    """This server's id on the list: made once, kept in the state dir."""
    p = state_dir() / "server-id"
    try:
        got = p.read_text().strip()
        if got:
            return got
    except OSError:
        pass
    sid = secrets.token_urlsafe(12)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(sid + "\n")
    return sid


def _seen_path():
    return state_dir() / "server-report.json"


def remember_base(base: str) -> None:
    """The address a sign-in came in on: the `api` fallback."""
    if not base:
        return
    try:
        p = _seen_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"base": base.rstrip("/")}))
    except OSError:
        pass


def addresses() -> dict:
    api = os.environ.get("MEDIA_PUBLIC_URL", "").strip()
    if not api:
        from . import tunnel
        api = tunnel.current_url()
    if not api:
        try:
            api = str(json.loads(_seen_path().read_text()).get("base") or "")
        except (OSError, ValueError):
            api = ""
    api = api.rstrip("/")
    web = os.environ.get("MEDIA_WEB_APP_URL", "").strip().rstrip("/")
    if not web and api:
        # The web app this server serves itself (web_app.py): on every
        # listener, so `/app` beside whichever address `api` is.
        from . import web_app
        if web_app.serving():
            web = api + web_app.PREFIX
    return {"api": api, "web": web}


#: At most this many of an account's devices go in a report, the latest seen first.
MAX_DEVICES = 10


def account_devices(device_id: str) -> list[dict]:
    """The devices here of the account that enrolled `device_id`, each with
    its name, when it was last seen and the app build it said it runs (no
    ids, tokens or addresses). [] for a device with no account."""
    rows = devices.list_devices()
    me = next((d for d in rows if d.get("id") == device_id), None)
    key = (str((me or {}).get("iss") or ""), str((me or {}).get("sub") or ""))
    if not all(key):
        return []
    mine = [d for d in rows if (str(d.get("iss") or ""), str(d.get("sub") or "")) == key]
    mine.sort(key=lambda d: float(d.get("last_seen") or 0), reverse=True)
    out = []
    for d in mine[:MAX_DEVICES]:
        row = {"name": str(d.get("name") or "device")[:80], "last_seen": int(float(d.get("last_seen") or 0))}
        if d.get("app"):
            row["app"] = {k: v for k, v in d["app"].items() if k in ("build", "sha", "platform", "shell")}
            row["app_at"] = int(float(d.get("app_at") or 0))
        out.append(row)
    return out


def account_role(device_id: str) -> str:
    """The account's role on this server: the highest any of its devices
    here has, as it takes effect (its ceiling applied). "" with no account."""
    rows = devices.list_devices()
    me = next((d for d in rows if d.get("id") == device_id), None)
    key = (str((me or {}).get("iss") or ""), str((me or {}).get("sub") or ""))
    if not all(key):
        return ""
    roles = [str(d.get("role") or "admin") for d in rows
             if (str(d.get("iss") or ""), str(d.get("sub") or "")) == key]
    ranked = [r for r in devices.ROLES if r in roles]
    return ranked[0] if ranked else ""


def paired_hosts() -> list[str]:
    """The names of the machines paired here as jobs workers (pair --jobs):
    the hosts this server restarts and updates down their own connection."""
    names = [str(d.get("name") or "").strip()[:64] for d in devices.list_devices()
             if devices.is_jobs_worker(d)]
    return sorted({n for n in names if n})


def describe(device_id: str = "") -> dict:
    body = {"id": server_id(), "name": os.environ.get("MEDIA_SERVER_NAME", "").strip() or socket.gethostname(),
            **addresses()}
    if device_id:
        body["devices"] = account_devices(device_id)
        role = account_role(device_id)
        if role:
            body["role"] = role
        body["hosts"] = paired_hosts()
    return body


def _post(iss: str, access: str, body: dict) -> tuple[int, dict]:
    """POST the report; `(status, answer)`, the answer {} when it is not JSON."""
    req = urllib.request.Request(f"{iss.rstrip('/')}/api/servers", data=json.dumps(body).encode(), method="POST",
                                 headers={"Authorization": f"Bearer {access}", "Accept": "application/json",
                                          "Content-Type": "application/json", "User-Agent": "agent-media servers"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            raw = r.read()
            status = r.status
    except urllib.error.HTTPError as e:
        return e.code, {}
    try:
        answer = json.loads(raw or b"{}")
    except ValueError:
        answer = {}
    return status, answer if isinstance(answer, dict) else {}


def _get(iss: str, access: str) -> tuple[int, dict]:
    """GET the account's servers from its issuer; `(status, answer)`."""
    req = urllib.request.Request(f"{iss.rstrip('/')}/api/servers", method="GET",
                                 headers={"Authorization": f"Bearer {access}", "Accept": "application/json",
                                          "User-Agent": "agent-media servers"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            raw = r.read()
            status = r.status
    except urllib.error.HTTPError as e:
        return e.code, {}
    try:
        answer = json.loads(raw or b"{}")
    except ValueError:
        answer = {}
    return status, answer if isinstance(answer, dict) else {}


def account_servers(device_id: str, iss: str) -> tuple[int, dict]:
    """The servers the account that enrolled `device_id` has a device on, as
    its issuer lists them (sasonica.com's Account page reads the same), each
    marked `this` when it is this server — for the app's "Pair another
    server", which offers the rest (David, 14 Nov 2026: a list in the app
    and a button on sasonica.com, "Both")."""
    from . import app

    status, answer = _get(iss, app._account_access(device_id, iss))
    if status in (401, 403):
        status, answer = _get(iss, app._account_access(device_id, iss, fresh=True))
    if status == 200 and isinstance(answer.get("servers"), list):
        mine = server_id()
        answer["servers"] = [{**s, "this": s.get("id") == mine}
                             for s in answer["servers"] if isinstance(s, dict)]
    return status, answer


def report(device_id: str) -> bool:
    """Tell the issuer of the account that enrolled this device about this
    server. False when it could not (no refresh token, issuer refused)."""
    from . import app, oidc

    iss, rt = devices.refresh_token_of(device_id)
    if not (iss and rt):
        return False
    body = describe(device_id)
    try:
        status, answer = _post(iss, app._account_access(device_id, iss), body)
        if status in (401, 403):
            status, answer = _post(iss, app._account_access(device_id, iss, fresh=True), body)
    except (oidc.OidcError, OSError) as e:
        print(f"servers: report for {device_id} failed: {e}", file=sys.stderr)
        return False
    if status != 200:
        print(f"servers: {iss} answered {status} for {device_id}", file=sys.stderr)
        return False
    keep_ceiling(device_id, answer)
    return True


def keep_ceiling(device_id: str, answer: dict) -> None:
    """The account's role ceiling from the issuer's answer, kept for
    devices.role_of. An issuer that sends none (one from before the
    ceiling) leaves what was kept."""
    ceiling = str(answer.get("role_ceiling") or "")
    if ceiling not in devices.ROLES:
        return
    me = next((d for d in devices.list_devices() if d.get("id") == device_id), None)
    iss, sub = str((me or {}).get("iss") or ""), str((me or {}).get("sub") or "")
    if devices.set_ceiling(iss, sub, ceiling):
        print(f"servers: {iss} caps account {sub} at {ceiling}", file=sys.stderr)


def report_soon(device_id: str, base: str = "") -> None:
    """After a sign-in: remember the address it came in on, report in the background."""
    remember_base(base)
    if enabled():
        threading.Thread(target=report, args=(device_id,), name="servers-report", daemon=True).start()


def report_all() -> int:
    """One report per signed-in account: through its most recently seen
    device that still holds a refresh token. How many went through."""
    best: dict[tuple[str, str], dict] = {}
    for d in devices.list_devices():
        key = (str(d.get("iss") or ""), str(d.get("sub") or ""))
        if not all(key) or not devices.refresh_token_of(str(d.get("id")))[1]:
            continue
        if key not in best or (d.get("last_seen") or 0) > (best[key].get("last_seen") or 0):
            best[key] = d
    return sum(report(str(d["id"])) for d in best.values())


def _loop() -> None:
    wait = _FIRST_S
    while True:
        threading.Event().wait(wait)
        wait = _PERIOD_S
        try:
            report_all()
        except Exception as e:  # noqa: BLE001 — the loop outlives a bad pass
            print(f"servers: daily report failed: {e}", file=sys.stderr)


def start() -> None:
    if _STARTED[0] or not enabled():
        return
    _STARTED[0] = True
    threading.Thread(target=_loop, name="servers-report", daemon=True).start()
