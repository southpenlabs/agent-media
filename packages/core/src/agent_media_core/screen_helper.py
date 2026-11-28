"""`media screen`: pause what plays on this computer while you talk to the
assistant or a reply is read out, and play it on after.

The desktop half of "Pause what's playing here" (server-contract §6.21f;
docs/proposals/2026-10-30-screens-that-pause.md, step 3). David, 28 Nov
2026: "Can we have a generic version of the pausing that was happening on
hpo for speech". hpo's PowerShell ducker (dotfiles/hpo/smtc) did this for
one machine with a hand-minted token; this does it on any Windows or Linux
desktop through the `audio_backend` seam (SMTC on Windows, MPRIS on Linux).

- `media screen pair '<sasonica://pair link>'`: this computer becomes a
  paired device of the server (a code from Settings → Devices → Pair). Then
  switch on "Pause what's playing here" for it in Settings → Devices.
- `media screen run`: the loop. Asks `GET /screen/now` every 300 ms while
  the switch is on, once a minute while it is off; while it says `pause`,
  pauses every playing player but Sasonica's own voice, and resumes those
  it paused once it says not (only those still paused: one the person
  played or stopped meanwhile is theirs).
- `media screen install`: run at login (a systemd user unit on Linux, a
  logon task in the desktop session on Windows, as SMTC refuses session 0).
- `media screen status`: paired or not, and the server's answer now.

Fails open: when the server cannot be reached for FAIL_OPEN_S while
paused, what was paused plays on.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable, Optional

from . import failures as _failures
from ._paths import state_dir

log = logging.getLogger("agent_media.screen")

UA = "agent-media-screen/1"
#: §6.21f: poll every 300 ms with the switch on, once a minute with it off.
POLL_ON_S = 0.3
POLL_OFF_S = 60.0
#: After a failed ask, wait this long before the next.
POLL_ERR_S = 5.0
#: Paused and unable to ask the server for this long: play on.
FAIL_OPEN_S = 3.0
UNIT = "agent-media-screen.service"
TASK = "sasonica-screen"


def conf_path() -> Path:
    return state_dir() / "screen.json"


def load() -> Optional[dict]:
    try:
        got = json.loads(conf_path().read_text())
    except (OSError, ValueError):
        return None
    if not isinstance(got, dict) or not got.get("url") or not got.get("token"):
        return None
    return got


def _save(rec: dict) -> Path:
    path = conf_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        json.dump(rec, fh, indent=2)
    os.replace(tmp, path)
    return path


class ScreenError(RuntimeError):
    pass


def _http(url: str, body: Optional[dict] = None, token: str = "",
          timeout: float = 4.0, method: str = "") -> tuple[int, dict]:
    headers = {"User-Agent": UA, "Accept": "application/json"}
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=data, headers=headers,
                                 method=method or ("POST" if body is not None else "GET"))
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            status, raw = r.status, r.read()
    except urllib.error.HTTPError as e:
        status, raw = e.code, e.read()
    except (OSError, ValueError) as e:
        raise ScreenError(f"could not reach {url}: {e}") from e
    try:
        got = json.loads(raw or b"{}")
    except ValueError:
        got = {"error": raw[:200].decode("utf-8", "replace")}
    return status, got if isinstance(got, dict) else {}


def pair(link: str, server: str = "", name: str = "") -> dict:
    """Trade a pairing code for a device token and keep it (0600)."""
    from .peer_link import parse_link

    link_server, code = parse_link(link)
    base = (server or link_server).rstrip("/")
    if not code:
        raise ScreenError("no pairing code in that link")
    if not base:
        raise ScreenError("the link names no server; pass --server URL")
    device = name or f"{socket.gethostname().split('.')[0]} screen"
    status, got = _http(base + "/pair", {"code": code, "device": device}, timeout=30.0)
    if status != 200 or not got.get("token"):
        raise ScreenError(f"{base}/pair said {status}: {got.get('error') or got}")
    rec = {"url": base, "token": got["token"], "device_id": got.get("device_id"),
           "name": got.get("name") or device,
           "paired_at": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
    _save(rec)
    # Say what kind of device this is (§6.59), so Settings shows a computer.
    try:
        _http(base + "/device/app", {"kind": "computer"},
              token=rec["token"])
    except ScreenError:
        pass
    return rec


def ask(rec: dict) -> dict:
    """The server's `GET /screen/now` for this device: {on, pause, why}."""
    status, got = _http(rec["url"] + "/screen/now", token=rec["token"])
    if status != 200 or not got.get("ok", True):
        raise ScreenError(f"/screen/now said {status}: {got.get('error') or got}")
    return got


class Ducker:
    """What is paused, and the rule for pausing and resuming. No I/O of its
    own beyond the backend, so it is tested with a fake one."""

    def __init__(self, backend, clock: Callable[[], float] = time.monotonic):
        self.backend = backend
        self.clock = clock
        self.paused: list[str] = []
        self.holding = False
        self.last_ok = clock()

    def step(self, pause: Optional[bool]) -> None:
        """One answer from the server: True, False, or None (no answer)."""
        now = self.clock()
        if pause is None:
            if self.holding and now - self.last_ok >= FAIL_OPEN_S:
                log.info("server unreachable while paused: playing on")
                self.release()
            return
        self.last_ok = now
        if pause and not self.holding:
            self.hold()
        elif not pause and self.holding:
            self.release()

    def hold(self) -> None:
        names = []
        for n in self.backend.playing_players():
            try:
                if self.backend.own_voice(n):
                    continue
            except Exception as e:  # noqa: BLE001 - unknown is not ours; pause it
                _failures.swallowed(e)
            names.append(n)
        if names:
            self.backend.pause_players(names)
            log.info("paused %s", names)
        self.paused = names
        self.holding = True

    def release(self) -> None:
        if self.paused:
            self.backend.resume_players(self.paused)
            log.info("resumed %s", self.paused)
        self.paused = []
        self.holding = False


def run(rec: dict, backend=None, sleep: Callable[[float], None] = time.sleep,
        once: bool = False) -> int:
    if backend is None:
        from .audio_backend import backend as _backend
        backend = _backend()
    d = Ducker(backend)
    last_err = None
    log.info("screen ducker started (%s, %s backend)", rec["url"], backend.name)
    while True:
        try:
            got = ask(rec)
            last_err = None
            d.step(bool(got.get("pause")))
            wait = POLL_ON_S if got.get("on") else POLL_OFF_S
        except ScreenError as e:
            if str(e) != last_err:
                log.warning("%s", e)
                last_err = str(e)
            d.step(None)
            # Keep asking quickly while something is paused, so it fails open on time.
            wait = POLL_ON_S if d.holding else POLL_ERR_S
        if once:
            d.release()
            return 0
        try:
            sleep(wait)
        except KeyboardInterrupt:
            d.release()
            return 0


# --- run at login -------------------------------------------------------

def _linux_unit(exe: list[str]) -> str:
    return f"""[Unit]
Description=Sasonica: pause this computer's media while you talk or a reply is read
After=graphical-session.target network-online.target

[Service]
ExecStart={' '.join(exe)} run
Restart=always
RestartSec=5

[Install]
WantedBy=graphical-session.target default.target
"""


def _self_cmd(windowless: bool = False) -> list[str]:
    exe = os.path.abspath(sys.executable)
    if windowless and os.name == "nt":
        w = Path(exe).with_name("pythonw.exe")
        if w.exists():
            exe = str(w)
    return [exe, "-m", "agent_media_core.screen_helper"]


def install(dry_run: bool = False) -> str:
    if sys.platform.startswith("linux"):
        unit_dir = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "systemd" / "user"
        text = _linux_unit(_self_cmd())
        if dry_run:
            return f"would write {unit_dir / UNIT}:\n{text}"
        unit_dir.mkdir(parents=True, exist_ok=True)
        (unit_dir / UNIT).write_text(text)
        subprocess.run(["systemctl", "--user", "daemon-reload"], check=False)
        subprocess.run(["systemctl", "--user", "enable", "--now", UNIT], check=False)
        return f"installed and started {UNIT}"
    if os.name == "nt":
        cmd = subprocess.list2cmdline(_self_cmd(windowless=True) + ["run"])
        create = ["schtasks", "/create", "/tn", TASK, "/tr", cmd, "/sc", "onlogon",
                  "/it", "/rl", "limited", "/f"]
        if dry_run:
            return "would run: " + subprocess.list2cmdline(create)
        subprocess.run(create, check=True)
        subprocess.run(["schtasks", "/run", "/tn", TASK], check=False)
        return f"installed logon task {TASK!r} and started it"
    raise ScreenError(f"no media control on {sys.platform}: nothing to install")


# --- command line -------------------------------------------------------

def add_args(p: argparse.ArgumentParser) -> None:
    sub = p.add_subparsers(dest="screen_cmd", required=True)
    pp = sub.add_parser("pair", help="pair this computer with a server (a code "
                                     "from Settings → Devices → Pair)")
    pp.add_argument("link", help="the sasonica://pair?server=…&code=… link, or the code")
    pp.add_argument("--server", metavar="URL", default="",
                    help="the server's base URL, when the link names none")
    pp.add_argument("--name", default="", help="the device's name (default: '<host> screen')")
    rp = sub.add_parser("run", help="the loop: pause and resume as the server says")
    rp.add_argument("--once", action="store_true", help="ask once and stop")
    ip = sub.add_parser("install", help="run at login (systemd user unit / logon task)")
    ip.add_argument("--dry-run", action="store_true")
    sub.add_parser("status", help="paired or not, and the server's answer now")


def cmd(a) -> int:
    if a.screen_cmd == "pair":
        try:
            rec = pair(a.link, server=a.server, name=a.name)
        except ScreenError as e:
            print(f"screen pair: {e}", file=sys.stderr)
            return 1
        print(f"paired with {rec['url']} as {rec['name']!r}; kept in {conf_path()}.\n"
              f"Now switch on \"Pause what's playing here\" for {rec['name']!r} in "
              f"Settings → Devices, then: media screen install")
        return 0
    if a.screen_cmd == "install":
        try:
            print(install(dry_run=a.dry_run))
        except (ScreenError, subprocess.CalledProcessError) as e:
            print(f"screen install: {e}", file=sys.stderr)
            return 1
        return 0
    conf = load()
    if conf is None:
        print("screen: not paired; run: media screen pair '<sasonica://pair link>'",
              file=sys.stderr)
        return 1
    if a.screen_cmd == "status":
        print(f"paired with {conf['url']} as {conf.get('name')!r}")
        try:
            got = ask(conf)
        except ScreenError as e:
            print(f"server: {e}")
            return 1
        print(f"switch {'on' if got.get('on') else 'OFF (Settings → Devices)'}; "
              f"pause now: {bool(got.get('pause'))}"
              + (f" ({got['why']})" if got.get("why") else ""))
        return 0
    from . import logs
    # Under pythonw (the Windows logon task) there is no stderr: log to a file.
    stream = sys.stderr if sys.stderr is not None else open(  # noqa: SIM115
        conf_path().with_name("screen.log"), "a", buffering=1, encoding="utf-8")
    logs.setup(stream=stream)
    return run(conf, once=a.once)


def main(argv: Optional[list[str]] = None) -> int:
    # A Windows console's code page cannot print "→"; never die over it.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")  # type: ignore[union-attr]
        except (AttributeError, ValueError):
            pass
    p = argparse.ArgumentParser(prog="media screen", description=__doc__.split("\n\n")[0])
    add_args(p)
    return cmd(p.parse_args(argv))


if __name__ == "__main__":
    sys.exit(main())
