"""A fenced practice copy of a server: the move's rehearsal (`media server sandbox`).

A restored copy of a live server, started while the real one still runs, is
not harmless (found 13 Nov 2026, before the first rehearsal): its session
host resumes every chat the snapshot caught working, so two agents do the
same work; a sign-in whose refresh token is good once (Trakt, Google) is
spent by whichever copy refreshes first, signing the other out; and it would
be a second David on Matrix, in reminders and on the calendar. David chose
the fence: "Sandbox on red2".

So the practice copy runs **beside** the machine's own install, never over it:

* restored into a throwaway folder (`~/.local/share/agent-media-move/sandbox`),
  the machine's own state, env file and units untouched — nothing to roll
  back, no quiet time needed;
* **fenced** before anything starts (`fence`): every chat's record is ended
  with nothing to resume, hold or deliver; records of chats on other hosts
  are dropped; every outside credential in the copy is deleted (tokens, the
  Matrix and Audiobookshelf settings, Google's client, secrets) and refresh
  tokens are blanked in the device list;
* started as two transient units of its own (`agent-media-rehearsal-sessiond`,
  `agent-media-rehearsal-canvas`) with their own socket, state and settings:
  not the restored env file (which names every outside service) but a few
  lines written here — the address, the public listener, reports to
  sasonica.com off, no default host.

What it proves: the backup restores on another machine as the same server
(`server-id`, the devices, the threads), it answers at a new address, a
device pairs and a new chat runs there. What it does not: the real units
(`media-setup install-services`), the house audio, any outside service.

    media server sandbox start --from <store> --name https://<address>
    media server sandbox status | stop
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

UNITS = ("agent-media-rehearsal-sessiond", "agent-media-rehearsal-canvas")
#: Outside credentials and service settings never kept in a practice copy.
CREDENTIALS = ("*token*", "matrix.env*", "abs-bridge.env*", "google-*", "secrets.env*",
               "calendar-publish*", "*.bak-*")
PUBLIC = "127.0.0.1:8789"
PORT = 8791


def root() -> Path:
    return Path(os.environ.get("MEDIA_SANDBOX_ROOT")
                or Path.home() / ".local" / "share" / "agent-media-move" / "sandbox")


def roots(base: Path | None = None) -> dict[str, Path]:
    base = base or root()
    home = base / "home"
    return {"home": home, "state": home / ".local" / "state" / "agent-media"}


def fence(base: Path | None = None) -> dict:
    """Make a restored copy safe to start beside the real server (see the
    module). Returns what it did, in counts."""
    r = roots(base)
    out = {"chats_ended": 0, "resumes_dropped": 0, "remote_records_dropped": 0,
           "credentials_deleted": [], "refresh_tokens_blanked": 0}
    sd = r["state"] / "sessiond"
    for p in sorted(sd.glob("*.json")) if sd.is_dir() else []:
        try:
            rec = json.loads(p.read_text())
        except (OSError, ValueError):
            continue
        if not isinstance(rec, dict) or not rec.get("session"):
            continue
        if rec.get("host"):
            p.unlink()
            out["remote_records_dropped"] += 1
            continue
        changed = False
        if rec.get("state") != "ended":
            rec["state"] = "ended"
            out["chats_ended"] += 1
            changed = True
        if rec.get("cut_off"):
            out["resumes_dropped"] += 1
        for k, empty in (("cut_off", None), ("held", []), ("queued", []), ("pending", []),
                         ("pid", None)):
            if rec.get(k):
                rec[k] = empty
                changed = True
        if changed:
            p.write_text(json.dumps(rec, indent=1) + "\n")
    # A stop mark or a pending restart would be read as "the stop cut these
    # off" and "deliver what was held".
    for p in (sd / "sessiond.stopped.json", r["state"] / "restart-pending.json"):
        p.unlink(missing_ok=True)
    for top in (r["state"], r["home"] / ".config" / "agent-media"):
        if not top.is_dir():
            continue
        for pat in CREDENTIALS:
            for p in top.rglob(pat):
                if p.is_file():
                    out["credentials_deleted"].append(str(p.relative_to(r["home"])))
                    p.unlink()
    env = r["home"] / ".config" / "agent-media.env"
    if env.exists():
        # Kept to read, never loaded: it names every outside service.
        env.rename(env.with_name("agent-media.env.restored-not-loaded"))
    dev = r["state"] / "devices.json"
    if dev.exists():
        try:
            data = json.loads(dev.read_text())
            out["refresh_tokens_blanked"] = _blank(data)
            dev.write_text(json.dumps(data, indent=1) + "\n")
        except (OSError, ValueError):
            pass
    return out


def _blank(node) -> int:
    n = 0
    if isinstance(node, dict):
        for k in list(node):
            # `rt` is a device's refresh token (devices.refresh_token_of).
            if (k == "rt" or "refresh" in str(k).lower()) and isinstance(node[k], str) \
                    and node[k]:
                node[k] = ""
                n += 1
            else:
                n += _blank(node[k])
    elif isinstance(node, list):
        for x in node:
            n += _blank(x)
    return n


def environment(name: str, base: Path | None = None) -> dict[str, str]:
    """What the two units run with: the copy's own state and settings."""
    base = base or root()
    r = roots(base)
    home = r["home"]
    bindir = str(Path(sys.executable).parent)
    envfile = base / "rehearsal.env"
    return {
        "XDG_STATE_HOME": str(home / ".local" / "state"),
        "XDG_CONFIG_HOME": str(home / ".config"),
        "XDG_CACHE_HOME": str(home / ".cache"),
        "MEDIA_ENV_FILE": str(envfile),
        "MEDIA_SESSIOND_SOCKET": str(base / "s.sock"),
        "MEDIA_PUBLIC_URL": name,
        "MEDIA_VISUAL_PUBLIC": PUBLIC,
        "MEDIA_UPLOAD_DIR": str(home / "shared"),
        "MEDIA_SERVERS_REPORT": "0",
        "MEDIA_SESSIOND_DEFAULT_HOST": "-",
        "MEDIA_JOBS_SESSIONS": "-",
        "MEDIA_HEADLESS": "1",
        "MEDIA_REHEARSAL": "1",
        "PATH": f"{bindir}:{Path.home()}/.local/share/fnm/aliases/default/bin:"
                f"{Path.home()}/.local/bin:/usr/local/bin:/usr/bin:/bin",
    }


def _systemctl(*args: str) -> tuple[int, str]:
    r = subprocess.run(["systemctl", "--user", *args], capture_output=True, text=True,
                       stdin=subprocess.DEVNULL)
    return r.returncode, (r.stdout + r.stderr).strip()


def stop(*, remove: bool = True) -> dict:
    for u in UNITS:
        _systemctl("stop", u)
        _systemctl("reset-failed", u)
    if remove and root().exists():
        shutil.rmtree(root())
    return {"ok": True, "stopped": list(UNITS), "removed": bool(remove)}


def status() -> dict:
    up = {u: _systemctl("is-active", u)[1] for u in UNITS}
    return {"ok": True, "root": str(root()), "there": root().exists(), "units": up,
            "running": all(v == "active" for v in up.values())}


def start(store: str, name: str) -> dict:
    """Restore the newest snapshot in `store` into the sandbox, fence it, and
    start its session host and server."""
    from . import backup, server_address

    name = server_address.origin(name)
    if not name:
        return {"ok": False, "error": "an address, please (https://name)"}
    stop()
    base = root()
    r = roots(base)
    r["state"].mkdir(parents=True)
    got = backup.restore("latest", dest=store, into=r, dry_run=False, force=True)
    if not got.get("ok", True) and got.get("error"):
        return {"ok": False, "error": f"the restore failed: {got.get('error')}"}
    fenced = fence(base)
    env = environment(name, base)
    (base / "rehearsal.env").write_text(
        "# The practice copy's own settings (server_sandbox.py). Not the restored env file.\n"
        + "".join(f"{k}={v}\n" for k, v in env.items() if k.startswith("MEDIA_")))
    bindir = Path(sys.executable).parent
    cmds = {UNITS[0]: [str(bindir / "media"), "sessiond"],
            UNITS[1]: [str(bindir / "media-visual-canvas"), "--bind", "127.0.0.1",
                       "--port", str(PORT)]}
    for unit, argv in cmds.items():
        run = ["systemd-run", "--user", "--unit", unit, "--collect",
               *[f"--setenv={k}={v}" for k, v in env.items()], *argv]
        rr = subprocess.run(run, capture_output=True, text=True, stdin=subprocess.DEVNULL)
        if rr.returncode != 0:
            return {"ok": False, "error": f"{unit} did not start: {(rr.stderr or rr.stdout).strip()}",
                    "fenced": fenced}
        time.sleep(2)
    time.sleep(3)
    st = status()
    return {"ok": st["running"], "name": name, "root": str(base), "fenced": fenced,
            "units": st["units"], "restored": len(got.get("restored") or []),
            "warnings": got.get("warnings") or [],
            **({} if st["running"] else {"error": "a unit is not running: journalctl --user -u "
                                                  + " -u ".join(UNITS)})}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="media server sandbox")
    sub = ap.add_subparsers(dest="cmd", required=True)
    st = sub.add_parser("start")
    st.add_argument("--from", dest="store", required=True)
    st.add_argument("--name", required=True)
    sub.add_parser("stop")
    sub.add_parser("status")
    a = ap.parse_args(argv)
    got = start(a.store, a.name) if a.cmd == "start" else stop() if a.cmd == "stop" else status()
    print(json.dumps(got, indent=1))
    return 0 if got.get("ok") else 1
