"""Where this server is reached, and giving it a new address.

The server moves home (docs/proposals/2026-11-12-the-server-moves-home.md,
"The migration feature"; David, 12 Nov 2026: "migrating a paired base needs
to be a simple process"). A server's public address lives in more places
than MEDIA_PUBLIC_URL: speech clip and book links, systemd drop-ins, the
tunnel's ingress. Renaming by hand means finding every one; missing one shows
up a week later as a clip that will not play. So:

* **`media server rename --to https://<new> [--also https://<old>]`** checks
  the new address reaches *this* server (`GET /server/probe`, a proof made
  from `server_id`, which a restore carries, so it is the same server at a
  new address, not a new one); then rewrites every setting whose value names
  the old address — the env file (`~/.config/agent-media.env`, or
  MEDIA_ENV_FILE) and the systemd user drop-ins (`*.service.d/*.conf`) —
  keeping a copy of each file it changes; adds the new and `--also` names to
  the tunnel's ingress when this host has `~/.cloudflared/config.yml`; keeps
  the old address as a previous name; reports the new one to the account's
  issuer (servers_report.py) under the unchanged id; and schedules a restart
  of every service (`media restart-services --after 10`), which is what
  makes the running services read it.
* **The server says it moved.** A request that arrives on a previous name
  (its `Host`) gets `moved_to` in `GET /features`, and `/sessions/events`
  opens with a `server_moved` frame (`{"base": <new>}`), so a phone still
  using the old address follows without anyone opening Settings. The device
  token is the server's, not the address's: a rename is never a re-pairing.
* **`media server check`** (and `media doctor`): the public URL is set, no
  setting still names a previous address or another host for the speech
  clips and books, and the tunnel routes the current name.

`media server status` says the address, the previous names and the id.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

#: Settings that must carry this server's own address (the app fetches them
#: from the phone): a different origin in one is a misconfiguration, not a
#: choice. Any other setting is changed only when it names the old address.
OWN_ADDRESS_KEYS = (
    "MEDIA_PUBLIC_URL",
    "MEDIA_SPEECH_CLIP_BASEURL_SASONICA",
    "MEDIA_SPEECH_CLIP_BASEURL_PHONE",
    "MEDIA_BOOK_BASEURL_PHONE",
)

_LINE = re.compile(r"^(\s*(?:export\s+)?)([A-Za-z_][A-Za-z0-9_]*)(=)(.*)$")
_UNIT_LINE = re.compile(r"^(\s*Environment=\"?)([A-Za-z_][A-Za-z0-9_]*)(=)(.*?)(\"?\s*)$")


# --- addresses -------------------------------------------------------------------------


def origin(url: str) -> str:
    """`https://host[:port]` of `url` (lower-cased host, no path), or ""."""
    url = (url or "").strip()
    if not url:
        return ""
    if "://" not in url:
        url = "https://" + url
    p = urllib.parse.urlsplit(url)
    if p.scheme not in ("http", "https") or not p.hostname:
        return ""
    host = p.hostname.lower()
    port = f":{p.port}" if p.port and p.port != (443 if p.scheme == "https" else 80) else ""
    return f"{p.scheme}://{host}{port}"


def host_of(url: str) -> str:
    o = origin(url)
    return urllib.parse.urlsplit(o).netloc if o else ""


def state_path() -> Path:
    from agent_media_core._paths import state_dir

    return state_dir() / "server-address.json"


def _state() -> dict:
    try:
        got = json.loads(state_path().read_text())
        return got if isinstance(got, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_state(st: dict) -> None:
    p = state_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(st, indent=1) + "\n")
    os.replace(tmp, p)


def current() -> str:
    """This server's public address (MEDIA_PUBLIC_URL), else the last rename's."""
    return origin(os.environ.get("MEDIA_PUBLIC_URL") or "") or origin(_state().get("current") or "")


def previous() -> list[str]:
    """The addresses this server answered on before (oldest first)."""
    return [o for o in (origin(x) for x in _state().get("previous") or []) if o]


def moved_to_for(host_header: str) -> str:
    """The current address when a request came in on a previous name, else ""."""
    host = (host_header or "").strip().lower()
    if not host:
        return ""
    now = current()
    if not now or host == host_of(now):
        return ""
    return now if host in {host_of(o) for o in previous()} else ""


# --- is it this server? -----------------------------------------------------------------


def proof(nonce: str) -> str:
    from .servers_report import server_id

    return hashlib.sha256(f"agent-media-server:{server_id()}:{nonce}".encode()).hexdigest()[:32]


def probe_answer(query: str) -> dict:
    """`GET /server/probe?nonce=` — the proof that this is the server a
    rename is run on (open: it says nothing but a hash of the id and nonce)."""
    nonce = (urllib.parse.parse_qs(query).get("nonce") or [""])[0][:64]
    if not nonce:
        return {"ok": False, "error": "nonce, please"}
    return {"ok": True, "proof": proof(nonce)}


def reaches_here(url: str, timeout: float = 15.0) -> tuple[bool, str]:
    """Whether `url` answers as this server: (yes, why not)."""
    nonce = secrets.token_hex(12)
    req = urllib.request.Request(f"{origin(url)}/server/probe?nonce={nonce}",
                                 headers={"User-Agent": "agent-media server rename"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            got = json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        return False, (f"{origin(url)} answered {e.code}" + (
            " — its server is older than this command, or it is another server"
            if e.code == 404 else ""))
    except (OSError, ValueError) as e:
        return False, f"{origin(url)} did not answer ({e})"
    if not isinstance(got, dict) or got.get("proof") != proof(nonce):
        return False, f"{origin(url)} answers, but as another server (its id differs)"
    return True, ""


# --- the files that hold the address ----------------------------------------------------


def env_file() -> Path:
    explicit = os.environ.get("MEDIA_ENV_FILE") or ""
    return Path(explicit) if explicit else Path.home() / ".config" / "agent-media.env"


def dropin_files() -> list[Path]:
    root = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "systemd" / "user"
    return sorted(root.glob("*.service.d/*.conf"))


def tunnel_config() -> Path:
    return Path(os.environ.get("MEDIA_TUNNEL_CONFIG") or Path.home() / ".cloudflared" / "config.yml")


def _settings(path: Path) -> list[tuple[int, str, str, re.Match]]:
    """(line index, key, value, match) of each setting in an env file or a drop-in."""
    try:
        lines = path.read_text().splitlines()
    except OSError:
        return []
    unit = path.suffix == ".conf"
    out = []
    for i, line in enumerate(lines):
        if line.lstrip().startswith("#"):
            continue
        m = (_UNIT_LINE if unit else _LINE).match(line)
        if m:
            out.append((i, m.group(2), m.group(4), m))
    return out


def _swap(value: str, old: str, new: str) -> str:
    """`value` with the origin `old` made `new`, only where it is the whole
    origin (`https://red5.x.com/…`, never `https://red5.x.com.evil/…`)."""
    return re.sub(re.escape(old) + r"(?=[/\"'\s?#]|$)", new, value)


def plan(old: str, new: str) -> list[dict]:
    """Every setting that names `old`: `{file, line, key, before, after}`."""
    out = []
    for path in [env_file(), *dropin_files()]:
        for i, key, value, _m in _settings(path):
            after = _swap(value, old, new)
            if after != value:
                out.append({"file": str(path), "line": i, "key": key, "before": value,
                            "after": after})
    return out


def _apply(changes: list[dict], stamp: str) -> list[str]:
    """Write `changes`, a copy of each file kept beside it first. The files written."""
    by_file: dict[str, list[dict]] = {}
    for c in changes:
        by_file.setdefault(c["file"], []).append(c)
    done = []
    for name, cs in by_file.items():
        path = Path(name)
        text = path.read_text()
        lines = text.splitlines()
        for c in cs:
            lines[c["line"]] = lines[c["line"]].replace(c["before"], c["after"], 1)
        shutil.copy2(path, path.with_name(f"{path.name}.bak-rename-{stamp}"))
        tmp = path.with_name(path.name + ".tmp-rename")
        tmp.write_text("\n".join(lines) + ("\n" if text.endswith("\n") else ""))
        os.chmod(tmp, path.stat().st_mode & 0o7777)
        os.replace(tmp, path)
        done.append(name)
    return done


def tunnel_hosts(path: Path | None = None) -> list[str] | None:
    """The hostnames the tunnel's ingress routes, or None with no config here."""
    path = path or tunnel_config()
    try:
        text = path.read_text()
    except OSError:
        return None
    return [h.lower() for h in re.findall(r"^\s*-?\s*hostname:\s*['\"]?([^'\"\s#]+)", text, re.M)]


def add_tunnel_hosts(hosts: list[str], stamp: str, path: Path | None = None) -> list[str]:
    """Give each of `hosts` missing from the ingress the first rule's service
    (written before the catch-all). The hosts added."""
    path = path or tunnel_config()
    have = tunnel_hosts(path)
    if have is None:
        return []
    want = [h for h in dict.fromkeys(x.lower() for x in hosts if x) if h not in have]
    if not want:
        return []
    lines = path.read_text().splitlines()
    first = next((i for i, ln in enumerate(lines) if re.match(r"^\s*-\s*hostname:", ln)), None)
    if first is None:
        return []
    indent = re.match(r"^(\s*)-", lines[first]).group(1)
    service = next((ln.split(":", 1)[1].strip() for ln in lines[first + 1:first + 4]
                    if ln.strip().startswith("service:")), "")
    if not service:
        return []
    at = next((i for i, ln in enumerate(lines) if i > first
               and re.match(r"^\s*-\s*service:", ln)), len(lines))
    rules = []
    for h in want:
        rules += [f"{indent}- hostname: {h}", f"{indent}  service: {service}"]
    lines[at:at] = rules
    shutil.copy2(path, path.with_name(f"{path.name}.bak-rename-{stamp}"))
    path.write_text("\n".join(lines) + "\n")
    return want


# --- the check ---------------------------------------------------------------------------


def problems() -> list[str]:
    """What disagrees about this server's address (empty: all agree)."""
    out = []
    now = current()
    if not now:
        return ["MEDIA_PUBLIC_URL is not set: the app and sasonica.com do not know this "
                "server's public address"]
    old = set(previous())
    for path in [env_file(), *dropin_files()]:
        for _i, key, value, _m in _settings(path):
            names = {origin(u) for u in re.findall(r"https?://[^\s\"'/?#]+", value)}
            stale = sorted(names & old)
            if stale:
                out.append(f"{key} in {path} still names {stale[0]} (this server is {now} now)")
            elif key in OWN_ADDRESS_KEYS and names and now not in names:
                out.append(f"{key} in {path} names {sorted(names)[0]}, not this server's {now}")
    hosts = tunnel_hosts()
    if hosts is not None and host_of(now) not in hosts:
        out.append(f"the tunnel ({tunnel_config()}) does not route {host_of(now)}")
    return out


# --- rename ------------------------------------------------------------------------------


def rename(new: str, also: list[str], *, check: bool = True, dry_run: bool = False,
           restart: bool = True, out=print) -> int:
    new_o = origin(new)
    if not new_o:
        out(f"server rename: {new!r} is not an address (https://name)")
        return 2
    old_o = current()
    if old_o == new_o:
        out(f"this server is already {new_o}")
        return 0
    also_o = [o for o in (origin(a) for a in also) if o and o != new_o]
    if old_o and old_o != new_o and old_o not in also_o:
        # The old name keeps answering unless it is retired by hand: the
        # thread links in the history and the app's App Links name it.
        also_o.append(old_o)
    changes = plan(old_o, new_o) if old_o and old_o != new_o else []
    if not any(c["key"] == "MEDIA_PUBLIC_URL" for c in changes):
        changes.append({"file": str(env_file()), "line": -1, "key": "MEDIA_PUBLIC_URL",
                        "before": "", "after": new_o})
    out(f"this server: {old_o or '(no public address yet)'} → {new_o}")
    for c in changes:
        out(f"  {c['key']}: {c['before'] or '(unset)'} → {c['after']}   ({c['file']})")
    hosts = tunnel_hosts()
    want = [host_of(new_o), *(host_of(a) for a in also_o)]
    missing = [h for h in want if hosts is not None and h not in hosts]
    if missing:
        out(f"  tunnel ingress gains: {', '.join(missing)}   ({tunnel_config()})")
    if also_o:
        out(f"  still answering on: {', '.join(also_o)} (requests there are told it moved)")
    if check:
        ok, why = reaches_here(new_o)
        if not ok:
            out(f"server rename: {why}.")
            out("  Point the name at this server first (the tunnel's ingress and its DNS "
                "record), or pass --no-check to rename anyway.")
            if missing:
                out("  Ingress rules to add to " + str(tunnel_config()) + ":")
                svc = "http://127.0.0.1:8789"
                for h in missing:
                    out(f"    - hostname: {h}\n      service: {svc}")
            return 1
        out(f"  {new_o} reaches this server.")
    if dry_run:
        out("dry run: nothing changed.")
        return 0
    stamp = time.strftime("%Y%m%d-%H%M%S")
    written = _apply([c for c in changes if c["line"] >= 0], stamp)
    if not any(c["key"] == "MEDIA_PUBLIC_URL" and c["line"] >= 0 for c in changes):
        path = env_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a") as fh:
            fh.write(f"\n# This server's public address (media server rename).\n"
                     f"MEDIA_PUBLIC_URL={new_o}\n")
        written.append(str(path))
    added = add_tunnel_hosts(want, stamp) if missing else []
    st = _state()
    prev = [o for o in previous() if o != new_o]
    for o in also_o:
        if o not in prev:
            prev.append(o)
    _save_state({**st, "current": new_o, "previous": prev, "renamed_at": round(time.time())})
    os.environ["MEDIA_PUBLIC_URL"] = new_o
    for name in dict.fromkeys(written):
        out(f"  wrote {name} (a copy kept as …bak-rename-{stamp})")
    if added:
        out(f"  tunnel ingress: added {', '.join(added)} — restart the tunnel "
            "(media restart-services --only tunnel --after 10) and route their DNS to it")
    try:
        from . import servers_report

        if not servers_report.enabled():
            raise RuntimeError("MEDIA_SERVERS_REPORT=0")
        n = servers_report.report_all()
        out(f"  told sasonica.com ({n} account{'s' if n != 1 else ''})")
    except Exception as e:  # noqa: BLE001 — the daily report catches up
        out(f"  sasonica.com not told now ({e}); the services report it after the restart")
    if not restart:
        out("restart every service to use the new address: media restart-services --after 10")
        return 0
    media = shutil.which("media") or str(Path(sys.executable).with_name("media"))
    r = subprocess.run([media, "restart-services", "--after", "10"], capture_output=True,
                       text=True, stdin=subprocess.DEVNULL)
    out("  " + ((r.stdout or r.stderr).strip().splitlines() or ["restart scheduled"])[-1])
    return 0 if r.returncode == 0 else 1


# --- the command -------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    # The pass-through subcommands take their own options, and argparse's
    # REMAINDER does not capture a leading one: `media server settle --stash`
    # died on the main parser ("unrecognized arguments: --stash --own") at the
    # red5 -> red2 cutover, 13 Nov 2026. Hand them their argv before the main
    # parser sees it. A leading `--` is dropped, so both spellings work.
    av = list(sys.argv[1:] if argv is None else argv)
    if av and av[0] in ("settle", "sandbox"):
        rest = av[1:]
        if rest and rest[0] == "--":
            rest = rest[1:]
        if av[0] == "settle":
            from . import server_settle

            return server_settle.main(rest)
        from . import server_sandbox

        return server_sandbox.main(rest)
    ap = argparse.ArgumentParser(prog="media server",
                                 description="this server's public address, and giving it a new one")
    sub = ap.add_subparsers(dest="cmd", required=True)
    rn = sub.add_parser("rename", help="give this server a new public address, every "
                                       "setting that names it with it")
    rn.add_argument("--to", required=True, help="the new address, e.g. https://red2-ryer.sasonica.com")
    rn.add_argument("--also", action="append", default=[],
                    help="another old address to keep answering (the current one always is)")
    rn.add_argument("--dry-run", action="store_true", help="say what would change, change nothing")
    rn.add_argument("--no-check", action="store_true",
                    help="do not first check that the new address reaches this server")
    rn.add_argument("--no-restart", action="store_true",
                    help="do not schedule the restart that makes the services read it")
    mv = sub.add_parser("move", help="move this server onto another machine: backup, restore "
                                     "there, rename, start (server_move.py)")
    mv.add_argument("--to", default="", help="the other machine, as ssh reaches it")
    mv.add_argument("--name", default="", help="the address the server has there")
    mv.add_argument("--also", action="append", default=[],
                    help="another old address to keep answering")
    mv.add_argument("--yes", action="store_true", help="do it (without: the steps, nothing done)")
    mv.add_argument("--dry-run", action="store_true")
    mv.add_argument("--foreground", action="store_true",
                    help="run every step from this terminal, not detached")
    mv.add_argument("--fire", action="store_true", help=argparse.SUPPRESS)
    mv.add_argument("--status", action="store_true", help="how the last move went")
    mv.add_argument("--verify", action="store_true", help="check the new address again")
    mv.add_argument("--rollback", action="store_true", help="undo the last move")
    mv.add_argument("--rehearse", action="store_true",
                    help="the practice move: a fenced copy on the other machine, "
                         "nothing stopped here (server_sandbox.py)")
    mv.add_argument("--rehearse-stop", action="store_true",
                    help="stop and remove the practice copy")
    stl = sub.add_parser("settle", add_help=False,
                         help="after a restore here: make the state this machine's")
    stl.add_argument("args", nargs=argparse.REMAINDER)
    mv.add_argument("--force", action="store_true",
                    help="move even while chats are working here (they are not resumed there)")
    sb = sub.add_parser("sandbox", add_help=False,
                        help="the practice copy on this machine: start, stop, status")
    sb.add_argument("args", nargs=argparse.REMAINDER)
    sub.add_parser("check", help="whether every setting agrees on this server's address")
    sub.add_parser("status", help="the address, the previous ones and the server's id")
    a = ap.parse_args(argv)
    if a.cmd == "rename":
        return rename(a.to, a.also, check=not a.no_check, dry_run=a.dry_run,
                      restart=not a.no_restart)
    if a.cmd == "settle":
        from . import server_settle

        return server_settle.main(a.args)
    if a.cmd == "sandbox":
        from . import server_sandbox

        return server_sandbox.main(a.args)
    if a.cmd == "move":
        from . import server_move

        if a.status:
            return server_move.status()
        if a.rollback:
            return server_move.rollback(force=a.force)
        if a.verify:
            return server_move.verify()
        if a.rehearse_stop:
            return server_move.rehearse_stop(a.to)
        if a.rehearse:
            return server_move.rehearse(a.to, a.name, yes=a.yes)
        return server_move.move(a.to, a.name, a.also, yes=a.yes, dry_run=a.dry_run,
                                foreground=a.foreground, fire=a.fire, force=a.force)
    if a.cmd == "check":
        bad = problems()
        for p in bad:
            print(f"✗ {p}")
        if not bad:
            print(f"✓ every setting agrees: this server is {current()}")
        return 1 if bad else 0
    from .servers_report import server_id

    print(f"address:  {current() or '(none: MEDIA_PUBLIC_URL is not set)'}")
    print(f"previous: {', '.join(previous()) or '(none)'}")
    print(f"id:       {server_id()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
