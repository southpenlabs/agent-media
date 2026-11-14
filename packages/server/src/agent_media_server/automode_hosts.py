"""Auto mode told which machines are the person's own, from their account.

docs/proposals/2026-11-14-hosts-for-auto-mode.md. Claude Code's auto mode
judges each action with a classifier that `permissions.allow` does not
reach; on 14 Nov 2026 it refused a copy from red5 to red2 as "unauthorized
access", not knowing both were David's. A hand-typed line saying so goes
stale the day a machine comes or goes, and cannot ship. This writes it
from what the account already knows:

* the servers the account has a device on, from its issuer
  (`servers_report.account_servers`: sasonica.com's `GET /api/servers`),
  keeping the ones where it is **admin** — the role each server reports,
  after sasonica.com's role ceiling. A server that reports another role is
  left out: somewhere the account may use is not one it administers. A
  server too old to report a role is kept (every role there defaults to
  admin), and this server's own role is read here;
* the machines paired to each as jobs workers (`hosts`), and the account's
  devices there (a phone).

One entry in `autoMode.environment`, starting with TAG, which is how a
refresh finds and rewrites it. It describes ownership and allows nothing:
what actions are fine stays the tools' own `allow` entries and Claude Code's
defaults. No addresses, only names.

Opt-in: `media-setup profile --only hosts` (or `media-setup hosts`) writes
it; the daily scheduler job (`media-setup hosts --refresh`) only rewrites a
line that is already there and never adds one. No answer from the issuer
leaves the last line as it was.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from . import devices, servers_report

TAG = "(agent-media hosts)"


def account_device() -> "tuple[str, str]":
    """`(device_id, iss)` of the account to ask about: an admin device here
    signed in with an account and still holding a refresh token, the most
    recently seen. ("", "") when there is none."""
    best = None
    for d in devices.list_devices():
        did = str(d.get("id") or "")
        if not (d.get("iss") and d.get("sub")) or d.get("role") != "admin":
            continue
        iss, rt = devices.refresh_token_of(did)
        if not (iss and rt):
            continue
        if best is None or float(d.get("last_seen") or 0) > float(best[2]):
            best = (did, iss, d.get("last_seen") or 0)
    return (best[0], best[1]) if best else ("", "")


def _names(rows, key: str = "") -> "list[str]":
    out = []
    for r in rows if isinstance(rows, list) else []:
        name = r.get(key) if key and isinstance(r, dict) else r
        name = " ".join(str(name or "").split())[:64]
        if name and name not in out:
            out.append(name)
    return out


def machines(servers: "list[dict]", *, this_role: str = "admin",
             local_hosts: "list[str] | None" = None) -> "list[dict]":
    """The servers the account administers, as `{name, this, hosts, devices}`.
    This server's own role and paired hosts are read here (`this_role`,
    `local_hosts`), not from its last report."""
    out = []
    for s in servers:
        if not isinstance(s, dict):
            continue
        role = s.get("role")
        if s.get("this"):
            role = this_role
        if role not in (None, "", "admin"):
            continue
        name = " ".join(str(s.get("name") or "").split())[:64]
        if not name:
            continue
        hosts = _names(local_hosts if s.get("this") and local_hosts is not None
                       else s.get("hosts"))
        # A server's own worker ("red2 jobs" on red2) is not another machine.
        hosts = [h for h in hosts if h.split()[0].lower() != name.split()[0].lower()]
        out.append({"name": name, "this": bool(s.get("this")), "hosts": hosts,
                    "devices": _names(s.get("devices"), "name")})
    return out


def line(found: "list[dict]") -> str:
    """The environment entry for these machines, or "" for none."""
    if not found:
        return ""
    parts = []
    for m in found:
        extra = []
        if m["hosts"]:
            extra.append("machines paired to it: " + ", ".join(m["hosts"]))
        if m["devices"]:
            extra.append("devices: " + ", ".join(m["devices"]))
        parts.append(m["name"] + (f" ({'; '.join(extra)})" if extra else ""))
    return (f"{TAG} These machines belong to this person's Sasonica account, "
            f"which administers each: {'; '.join(parts)}. The same person owns "
            "them all; reaching one from another (ssh, scp, rsync, a server's "
            "own jobs) is administering their own machines, not someone else's.")


def build() -> "tuple[str, str]":
    """`(line, why_not)`: the line from the account's issuer, or "" and why."""
    did, iss = account_device()
    if not did:
        return "", "no admin device here is signed in with an account"
    try:
        status, answer = servers_report.account_servers(did, iss)
    except Exception as e:  # noqa: BLE001 — the issuer being away is not an error here
        return "", f"the account's issuer did not answer: {e}"
    if status != 200 or not isinstance(answer.get("servers"), list):
        return "", f"the account's issuer answered {status}"
    me = next((d for d in devices.list_devices() if d.get("id") == did), None)
    found = machines(answer["servers"], this_role=str((me or {}).get("role") or "admin"),
                     local_hosts=servers_report.paired_hosts())
    text = line(found)
    return (text, "") if text else ("", "the account administers no server")


def current(settings: dict) -> str:
    env = ((settings.get("autoMode") or {}).get("environment") or []) if isinstance(
        settings.get("autoMode"), dict) else []
    return next((e for e in env if isinstance(e, str) and e.startswith(TAG)), "")


def merge(settings: dict, text: str) -> "tuple[dict, bool]":
    """`settings` with `text` as the tagged environment entry (after
    `$defaults` in a list it starts), anything else kept."""
    settings = json.loads(json.dumps(settings))
    auto = settings.get("autoMode")
    auto = auto if isinstance(auto, dict) else {}
    cur = auto.get("environment")
    cur = list(cur) if isinstance(cur, list) else ["$defaults"]
    new = [e for e in cur if not (isinstance(e, str) and e.startswith(TAG))] + [text]
    if new == cur:
        return settings, False
    auto["environment"] = new
    settings["autoMode"] = auto
    return settings, True


def _write(path: Path, settings: dict) -> None:
    """Through a temporary file, checked, then moved: a settings.json is never
    left empty or half written (14 Nov 2026: a redirect onto the file being
    read emptied one)."""
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(settings, indent=2) + "\n")
    json.loads(tmp.read_text())
    if path.exists():
        import shutil
        shutil.copy2(path, str(path) + ".bak")
    os.replace(tmp, path)


def apply(path: Path, *, add: bool, dry_run: bool = False) -> "tuple[str, str]":
    """Write or refresh the line in `path`. `(state, detail)`: "installed",
    "ok" (unchanged), "absent" (not opted in, on a refresh), "kept" (no
    answer: the last line stays) or "failed"."""
    try:
        settings = json.loads(path.read_text()) if path.exists() else {}
    except (OSError, ValueError) as e:
        return "failed", f"cannot read {path}: {e}"
    if not isinstance(settings, dict):
        return "failed", f"{path} is not a JSON object"
    had = current(settings)
    if not had and not add:
        return "absent", "opt-in: media-setup profile --only hosts"
    text, why = build()
    if not text:
        return ("kept", why) if had else ("failed", why)
    merged, changed = merge(settings, text)
    if not changed:
        return "ok", str(path)
    if dry_run:
        print(f"# would write {path}:\n  environment: {text}")
        return "installed", str(path)
    print(f"  auto mode will be told (autoMode in {path}):\n    environment: {text}")
    _write(path, merged)
    return "installed", str(path)
