"""After a restore on the machine a server moved to: make the state this
machine's (`media server settle`, a step of `media server move`).

A restore brings the old machine's state as it was. Four things in it are
wrong on the new machine, each found by the move's rehearsal (13 Nov 2026):

* **Chats that ran here as the old server's worker.** The old server kept a
  record of each (`host: <this machine>`), and the restore lays it over this
  machine's own record of the same chat, the real one. `stash` copies this
  machine's records aside before the restore; `settle` puts each back in
  place of the old server's, without `host`, and drops the mirror of its
  transcript (the transcript itself is here already).
* **Chats the stop cut off.** The session host resumes those at its next
  start, which on the new machine means an agent picking up mid-task in a
  different working tree, without the old one's uncommitted files. They are
  ended instead, nothing resumed, and listed (`moved-chats.json` in the
  state dir) so their owner can pick them up. Messages held for them stay.
* **A default host naming this machine** (MEDIA_SESSIOND_DEFAULT_HOST): the
  chats run here now, so the line is commented out of the env file.
* **Folders.** A chat's folder that is not on this machine cannot be resumed
  until it is; they are counted by folder, most used first.

Returns what it did. Nothing here names a host: "this machine" is its own
hostname (`sessiond_remote.is_local`).
"""

from __future__ import annotations

import argparse
import collections
import ipaddress
import json
import os
import re
import shutil
import socket
import time
from pathlib import Path


def _state() -> Path:
    from agent_media_core._paths import state_dir

    return state_dir()


def _records(sd: Path):
    for p in sorted(sd.glob("*.json")) if sd.is_dir() else []:
        if p.name.startswith((".", "sessiond.")):
            continue
        try:
            rec = json.loads(p.read_text())
        except (OSError, ValueError):
            continue
        if isinstance(rec, dict) and rec.get("session"):
            yield p, rec


def stash(own: Path) -> dict:
    """This machine's own session records, copied aside before a restore."""
    sd = _state() / "sessiond"
    own.mkdir(parents=True, exist_ok=True)
    n = 0
    for p, _rec in _records(sd):
        shutil.copy2(p, own / p.name)
        n += 1
    # Its env file too: a few of its settings are about this machine, not
    # the server, and the restore replaces the file (`HOST_KEYS`).
    from .server_address import env_file

    try:
        shutil.copy2(env_file(), own / "agent-media.env.before")
    except OSError:
        pass
    return {"ok": True, "stashed": n, "own": str(own)}


#: Settings that describe the machine, kept from its own env file when the
#: restored one lacks them: where its agent CLI is (red2's `claude` is under
#: fnm, on no PATH a unit has).
HOST_KEYS = ("MEDIA_SESSIOND_CLAUDE",)


def keep_host_keys(own: Path | None) -> list[str]:
    from .server_address import env_file

    if own is None or not (own / "agent-media.env.before").exists():
        return []
    before = dict(m.groups() for m in re.finditer(
        r"^\s*(?:export\s+)?([A-Z0-9_]+)=(.*)$", (own / "agent-media.env.before").read_text(),
        re.M))
    env = env_file()
    try:
        now = env.read_text()
    except OSError:
        now = ""
    add = [(k, before[k]) for k in HOST_KEYS
           if k in before and not re.search(rf"^\s*(?:export\s+)?{k}=", now, re.M)]
    if add:
        with open(env, "a") as fh:
            fh.write("\n# This machine's own, kept across the restore (media server settle).\n"
                     + "".join(f"{k}={v}\n" for k, v in add))
    return [k for k, _v in add]


_TAILNET = ipaddress.ip_network("100.64.0.0/10")
_BIND = re.compile(r"^(\s*(?:export\s+)?)(MEDIA_[A-Z0-9_]*BIND)=(.*)$")


def _bindable(ip: str) -> bool:
    """Whether a listener can be opened on `ip` here: asked of the kernel,
    which is the one that refuses it (EADDRNOTAVAIL)."""
    try:
        fam = socket.AF_INET6 if ":" in ip else socket.AF_INET
        with socket.socket(fam, socket.SOCK_STREAM) as sk:
            sk.bind((ip, 0))
        return True
    except OSError:
        return False


def _tailnet_here() -> str:
    try:
        from agent_media_core.setup import _tailnet_address

        return (_tailnet_address() or "").strip()
    except Exception:
        return ""


def move_binds(lines: list[str], *, bindable=_bindable, tailnet=_tailnet_here) -> tuple[list, list]:
    """The listeners' addresses, made this machine's (in `lines`, in place).

    A `MEDIA_*_BIND` is an address of the machine, and a restore carries the
    old machine's: the share listener crash-looped on red2 with "Cannot
    assign requested address", bound to red5's tailnet address (the cutover,
    13 Nov 2026). `rename` rewrites the server's public name, never these.

    An address this machine has is left alone, and so are loopback, a
    wildcard and a name. A tailnet address it has not becomes its own
    tailnet address: the same listener, as private as it was. Anything else
    it cannot bind is not guessed at; it is said.
    """
    moved, warnings, ts = [], [], None
    for i, line in enumerate(lines):
        m = _BIND.match(line)
        if not m:
            continue
        lead, key, raw = m.groups()
        val = raw.strip().strip("\"'")
        try:
            ip = ipaddress.ip_address(val)
        except ValueError:
            continue
        if ip.is_loopback or ip.is_unspecified or bindable(val):
            continue
        if ip.version == 4 and ip in _TAILNET:
            if ts is None:
                ts = tailnet()
            if ts and ts != val and bindable(ts):
                lines[i] = f"{lead}{key}={ts}"
                lines.insert(i, f"# (media server settle: was {val}, the old machine's "
                                "tailnet address)")
                moved.append({"key": key, "was": val, "now": ts})
                continue
        warnings.append(f"{key}={val} is not an address of this machine: its listener cannot "
                        "start until it is set")
    # The old addresses elsewhere (a base URL built on one): said, not rewritten.
    for old in sorted({b["was"] for b in moved}):
        for line in lines:
            k = re.match(r"^\s*(?:export\s+)?([A-Z0-9_]+)=", line)
            if k and old in line and not _BIND.match(line):
                warnings.append(f"{k.group(1)} still names {old}, the old machine's address")
    return moved, warnings


def settle(own: Path | None = None) -> dict:
    from .sessiond import LIVE_STATES
    from .sessiond_remote import is_local
    from .server_address import env_file

    state = _state()
    sd = state / "sessiond"
    out: dict = {"ok": True, "adopted": 0, "adopted_without_own": 0, "not_resumed": [],
                 "default_host_unset": False, "folders_missing": [], "kept_own": 0}
    seen: set[str] = set()
    missing: collections.Counter = collections.Counter()
    for p, rec in _records(sd):
        sid = str(rec["session"])
        seen.add(p.name)
        if rec.get("host") and is_local(str(rec["host"])):
            mine = own / p.name if own else None
            if mine is not None and mine.exists():
                shutil.copy2(mine, p)
                out["adopted"] += 1
            else:
                # No record of ours: keep the thread, ended, as a local one.
                rec = {k: v for k, v in rec.items() if k not in ("host", "remote_cwd", "seq_seen")}
                rec.update(state="ended", pid=None, cut_off=None)
                if rec.get("cwd"):
                    rec["cwd"] = os.path.expanduser(str(rec["cwd"]))
                p.write_text(json.dumps(rec, indent=1) + "\n")
                out["adopted_without_own"] += 1
            (sd / "remote" / f"{sid}.jsonl").unlink(missing_ok=True)
            continue
        if rec.get("host"):
            continue
        changed = False
        if rec.get("cut_off") or rec.get("state") in LIVE_STATES:
            if rec.get("cut_off") or rec.get("state") in ("working", "starting", "approval"):
                out["not_resumed"].append({
                    "session": sid, "cwd": rec.get("cwd"), "state": rec.get("state"),
                    "why": (rec.get("cut_off") or {}).get("why"),
                    "first_text": " ".join(str(rec.get("first_text") or "").split())[:80]})
            rec.update(state="ended", cut_off=None, pid=None, queued=[], pending=[])
            changed = True
        cwd = str(rec.get("cwd") or "")
        if cwd and not os.path.isdir(cwd):
            missing[cwd] += 1
        if changed:
            p.write_text(json.dumps(rec, indent=1) + "\n")
    # This machine's own chats the old server never knew: kept as they were.
    if own and own.is_dir():
        for mine in sorted(own.glob("*.json")):
            if mine.name not in seen and not (sd / mine.name).exists():
                sd.mkdir(parents=True, exist_ok=True)
                shutil.copy2(mine, sd / mine.name)
                out["kept_own"] += 1
    # The old machine's stop mark and pending restart mean nothing here.
    (sd / "sessiond.stopped.json").unlink(missing_ok=True)
    (state / "restart-pending.json").unlink(missing_ok=True)
    if out["not_resumed"]:
        (state / "moved-chats.json").write_text(json.dumps(
            {"at": round(time.time()), "not_resumed": out["not_resumed"]}, indent=1) + "\n")
    out["folders_missing"] = [{"cwd": c, "chats": n} for c, n in missing.most_common()]

    env = env_file()
    try:
        lines = env.read_text().splitlines()
    except OSError:
        lines = []
    for i, line in enumerate(lines):
        m = re.match(r"^\s*(?:export\s+)?MEDIA_SESSIOND_DEFAULT_HOST=(.*)$", line)
        if m and is_local(m.group(1).strip().strip("\"'")):
            lines[i] = ("# (media server settle: the chats run here now) " + line)
            out["default_host_unset"] = True
    out["binds_moved"], bind_warnings = move_binds(lines)
    if out["default_host_unset"] or out["binds_moved"]:
        env.write_text("\n".join(lines) + "\n")
    out["host_keys_kept"] = keep_host_keys(own)
    # A backup onto a path of the old machine's, or to this machine itself,
    # protects nothing here: said, not guessed at.
    out["warnings"] = list(bind_warnings)
    text = "\n".join(lines)
    m = re.search(r"^MEDIA_BACKUP_DEST=(.*)$", text, re.M)
    if m and ":" not in m.group(1) and not os.path.isdir(os.path.expanduser(m.group(1).strip())):
        out["warnings"].append(f"MEDIA_BACKUP_DEST={m.group(1).strip()} is not a folder on this "
                               "machine: the nightly backup has nowhere to go until it is set")
    m = re.search(r"^MEDIA_BACKUP_REPO=\S*?:([A-Za-z0-9_.-]+):", text, re.M)
    if m and is_local(m.group(1)):
        out["warnings"].append("MEDIA_BACKUP_REPO names this machine itself: the encrypted "
                               "history would sit beside the data it protects")
    return out


def install_units(src: Path, run=None) -> dict:
    """The old machine's unit files (`server_move.collect_units`) onto this
    one: each that is not here already is written (one `media-setup
    install-services` wrote stays: it names the release), and those enabled
    there are enabled here. `media-setup release-mode` afterwards points the
    carried ones at the release."""
    import subprocess

    from .server_move import units_dir

    if run is None:
        def run(argv):
            r = subprocess.run(argv, capture_output=True, text=True)
            return r.returncode, (r.stderr or r.stdout).strip()
    dst = units_dir()
    dst.mkdir(parents=True, exist_ok=True)
    out: dict = {"ok": True, "written": [], "kept": [], "enabled": [], "not_enabled": []}
    for p in sorted(src.glob("*")) if src.is_dir() else []:
        if p.suffix not in (".service", ".timer"):
            continue
        if (dst / p.name).exists():
            out["kept"].append(p.name)
        else:
            (dst / p.name).write_text(p.read_text())
            out["written"].append(p.name)
    run(["systemctl", "--user", "daemon-reload"])
    try:
        want = json.loads((src / "enabled.json").read_text())
    except (OSError, ValueError):
        want = []
    for name in want:
        if not (dst / name).exists():
            continue
        rc, err = run(["systemctl", "--user", "enable", name])
        (out["enabled"] if rc == 0 else out["not_enabled"]).append(
            name if rc == 0 else f"{name}: {err[-120:]}")
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="media server settle")
    ap.add_argument("--own", default="", help="where this machine's own records were stashed")
    ap.add_argument("--stash", action="store_true",
                    help="copy this machine's records to --own (before the restore)")
    ap.add_argument("--units", default="",
                    help="install the old machine's unit files from this folder, nothing else")
    a = ap.parse_args(argv)
    if a.units:
        got = install_units(Path(os.path.expanduser(a.units)))
        print(json.dumps(got, indent=1))
        return 0
    own = Path(os.path.expanduser(a.own)) if a.own else None
    if a.stash:
        if own is None:
            ap.error("--stash needs --own DIR")
        got = stash(own)
    else:
        got = settle(own)
    print(json.dumps(got, indent=1))
    return 0 if got.get("ok") else 1
