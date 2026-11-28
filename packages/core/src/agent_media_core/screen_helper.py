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
import threading
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
          timeout: float = 4.0, method: str = "",
          extra: Optional[dict] = None) -> tuple[int, dict]:
    headers = {"User-Agent": UA, "Accept": "application/json", **(extra or {})}
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
    # The version rides along, so media doctor can say when a helper stops updating.
    status, got = _http(rec["url"] + "/screen/now", token=rec["token"],
                        extra={"X-Screen-Helper": installed_version() or "unknown"})
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


# --- this computer's own microphone ------------------------------------
#
# Dictation on the computer itself (hpo's Super+Insert voice typing, Windows'
# Win+H, a browser's mic) pauses it too (David, 28 Nov 2026: "Can we also make
# it pause for dictation"). Not one dictation tool wired in, but the mic being
# open at all, as the OS reports it: Windows keeps it per app in the privacy
# store (CapabilityAccessManager, LastUsedTimeStop 0 = in use; WSL's mic shows
# as msrdc.exe), PulseAudio/PipeWire as recording streams. An app that holds
# the mic for good is left out with MEDIA_SCREEN_MIC_IGNORE (names, commas);
# MEDIA_SCREEN_MIC=0 turns this off.

_MIC_KEY = (r"Software\Microsoft\Windows\CurrentVersion\CapabilityAccessManager"
            r"\ConsentStore\microphone")


def _mic_ignored(name: str) -> bool:
    raw = os.environ.get("MEDIA_SCREEN_MIC_IGNORE", "")
    low = name.lower()
    return any(x.strip() and x.strip().lower() in low for x in raw.split(","))


def _windows_mic_users() -> list[str]:
    import winreg  # type: ignore[import-not-found,unused-ignore]

    users: list[str] = []

    def walk(key, path: str) -> None:
        i = 0
        while True:
            try:
                sub = winreg.EnumKey(key, i)
            except OSError:
                break
            i += 1
            try:
                with winreg.OpenKey(key, sub) as k:
                    try:
                        stop, _ = winreg.QueryValueEx(k, "LastUsedTimeStop")
                        start, _ = winreg.QueryValueEx(k, "LastUsedTimeStart")
                        if int(stop) == 0 and int(start) != 0:
                            users.append(sub.replace("#", "\\"))
                    except OSError:
                        pass
                    walk(k, path + "\\" + sub)
            except OSError:
                continue

    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _MIC_KEY) as root:
        walk(root, _MIC_KEY)
    return users


def _linux_mic_users() -> list[str]:
    try:
        r = subprocess.run(["pactl", "-f", "json", "list", "source-outputs"],
                           capture_output=True, text=True, timeout=2)
    except (OSError, subprocess.SubprocessError):
        return []
    if r.returncode != 0:
        return []
    try:
        rows = json.loads(r.stdout or "[]")
    except ValueError:
        return []
    users = []
    for row in rows if isinstance(rows, list) else []:
        props = row.get("properties") or {}
        # A level meter or a monitor of an output is not someone talking.
        if str(props.get("media.role", "")).lower() == "production" or \
                str(row.get("source", "")).endswith(".monitor"):
            continue
        users.append(str(props.get("application.process.binary")
                         or props.get("application.name") or "?"))
    return users


def mic_users() -> list[str]:
    """The apps holding this computer's microphone now (ignored ones left out)."""
    if os.environ.get("MEDIA_SCREEN_MIC", "1").strip() in ("0", "false", "no", "off"):
        return []
    try:
        if os.name == "nt":
            got = _windows_mic_users()
        elif sys.platform.startswith("linux"):
            got = _linux_mic_users()
        else:
            return []
    except Exception as e:  # noqa: BLE001 - no mic answer is no mic pause
        _failures.swallowed(e)
        return []
    return [u for u in got if not _mic_ignored(u)]


# --- updating itself ------------------------------------------------------
#
# David, 28 Nov 2026: "Make it update itself". The paired server hands out
# the core wheel of the release it runs (GET /screen/helper, /wheel;
# agent-media-server screen_dist.py). Checked a minute after start and every
# UPDATE_EVERY_S; a new version is downloaded, its sha256 checked, installed
# into this venv, and the helper starts again. Only a helper installed from a
# wheel updates (an editable checkout is left alone); MEDIA_SCREEN_UPDATE=0
# turns it off.

UPDATE_FIRST_S = 60.0
UPDATE_EVERY_S = 6 * 3600.0
_NO_WINDOW = 0x08000000           # CREATE_NO_WINDOW
_DETACHED = 0x00000008 | 0x00000200   # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP


def _version_path() -> Path:
    return conf_path().with_name("screen-version")


def installed_version() -> str:
    try:
        return _version_path().read_text().strip()
    except OSError:
        return ""


def may_update() -> bool:
    if os.environ.get("MEDIA_SCREEN_UPDATE", "1").strip() in ("0", "false", "no", "off"):
        return False
    here = Path(__file__).resolve()
    return Path(sys.prefix).resolve() in here.parents   # installed, not a checkout


def _console_python() -> str:
    exe = Path(os.path.abspath(sys.executable))
    if exe.name.lower() == "pythonw.exe" and exe.with_name("python.exe").exists():
        return str(exe.with_name("python.exe"))
    return str(exe)


def _install_cmds(wheel: Path) -> list[list[str]]:
    py = _console_python()
    try:
        import pip  # noqa: F401
        base = [py, "-m", "pip", "install", "--disable-pip-version-check", "-q"]
    except ImportError:
        uv = shutil.which("uv") or str(Path.home() / ".local" / "bin" / "uv")
        base = [uv, "pip", "install", "--python", py, "-q"]
    # The same version number every build: forced in, then its dependencies met.
    return [base + ["--force-reinstall", "--no-deps", str(wheel)], base + [str(wheel)]]


def check_update(rec: dict, run_cmd: Callable[..., subprocess.CompletedProcess] = subprocess.run) -> bool:
    """Install the server's newer helper, if there is one. True when installed."""
    import hashlib
    import tempfile

    status, got = _http(rec["url"] + "/screen/helper", token=rec["token"], timeout=600)
    if status != 200 or not got.get("version"):
        if status != 404:   # 404: the server runs no release; nothing to hand out
            log.info("update check: %s %s", status, got.get("error") or "")
        return False
    ver = str(got["version"])
    if ver == installed_version():
        return False
    req = urllib.request.Request(rec["url"] + "/screen/helper/wheel",
                                 headers={"User-Agent": UA, "Authorization": f"Bearer {rec['token']}"})
    with urllib.request.urlopen(req, timeout=600) as r:
        data = r.read()
        name = (r.headers.get("Content-Disposition") or "").partition('filename="')[2].rstrip('"')
    if hashlib.sha256(data).hexdigest() != got.get("sha256"):
        raise ScreenError("update: the wheel's sha256 does not match")
    if not name.endswith(".whl"):
        name = "agent_media_core-0.0.0-py3-none-any.whl"
    with tempfile.TemporaryDirectory() as tmp:
        whl = Path(tmp) / Path(name).name
        whl.write_bytes(data)
        kw = {"creationflags": _NO_WINDOW} if os.name == "nt" else {}
        for c in _install_cmds(whl):
            r2 = run_cmd(c, capture_output=True, text=True, timeout=900, **kw)
            if r2.returncode != 0:
                raise ScreenError(f"update: install failed: {(r2.stderr or r2.stdout)[-300:]}")
    _version_path().parent.mkdir(parents=True, exist_ok=True)
    _version_path().write_text(ver + "\n")
    log.info("updated to %s", ver)
    return True


def _start_again() -> None:
    """Hand over to a fresh process of the new code. Linux: systemd's
    Restart=always does it once this one exits. Windows: start one, detached,
    in this desktop session, through the launcher when there is one."""
    if os.name == "nt":
        cmd = _launch_cmd(windowless=True) if launcher_path().exists() \
            else _self_cmd(windowless=True) + ["run"]
        subprocess.Popen(cmd, creationflags=_DETACHED | _NO_WINDOW, close_fds=True)


# --- the launcher (agent-mail #1449) --------------------------------------
#
# The task or unit runs screen-launch.py, a copy of screen_launch.py kept
# beside screen.json, outside the install it heals: it checks the install
# against its RECORD, runs `… screen_helper run`, and reinstalls the
# server's wheel when a start fails (screen_launch.py says how).

def launcher_path() -> Path:
    return conf_path().with_name("screen-launch.py")


def ensure_launcher() -> bool:
    """Write (or refresh) the launcher copy; True when it changed."""
    src = Path(__file__).with_name("screen_launch.py").read_text(encoding="utf-8")
    dest = launcher_path()
    try:
        if dest.read_text(encoding="utf-8") == src:
            return False
    except OSError:
        pass
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".tmp")
    tmp.write_text(src, encoding="utf-8")
    os.replace(tmp, dest)
    return True


def _launch_cmd(windowless: bool = False) -> list[str]:
    return [_self_cmd(windowless)[0], str(launcher_path())]


def migrate_to_launcher(run_cmd: Callable[..., subprocess.CompletedProcess] = subprocess.run) -> str:
    """An install from before the launcher: its task or unit pointed at the
    launcher, without starting anything (this process is the one running).
    "" when nothing was done, else what was."""
    ensure_launcher()
    name = launcher_path().name
    if sys.platform.startswith("linux"):
        unit = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "systemd" / "user" / UNIT
        try:
            text = unit.read_text()
        except OSError:
            return ""
        if name in text:
            return ""
        unit.write_text(_linux_unit(_launch_cmd()))
        run_cmd(["systemctl", "--user", "daemon-reload"], check=False)
        return f"{UNIT} now starts through {name}"
    if os.name == "nt":
        q = run_cmd(["schtasks", "/query", "/tn", TASK, "/xml"], capture_output=True, text=True,
                    creationflags=_NO_WINDOW)
        if q.returncode != 0 or name in (q.stdout or ""):
            return ""
        cmd = subprocess.list2cmdline(_launch_cmd(windowless=True))
        run_cmd(["schtasks", "/create", "/tn", TASK, "/tr", cmd, "/sc", "onlogon", "/it",
                 "/rl", "limited", "/f"], check=False, creationflags=_NO_WINDOW)
        run_cmd(["powershell", "-NoProfile", "-Command", _TASK_SETTINGS], check=False,
                creationflags=_NO_WINDOW)
        return f"task {TASK!r} now starts through {name}"
    return ""


def run(rec: dict, backend=None, sleep: Callable[[float], None] = time.sleep,
        once: bool = False, mic: Callable[[], list[str]] = mic_users,
        updates: bool = True, clock: Callable[[], float] = time.monotonic) -> int:
    if backend is None:
        from .audio_backend import backend as _backend
        backend = _backend()
    d = Ducker(backend)
    last_err = None
    on = False
    last_mic: list[str] = []
    log.info("screen ducker started (%s, %s backend, version %s)", rec["url"],
             backend.name, installed_version() or "unknown")
    next_update = (clock() + UPDATE_FIRST_S) if (updates and may_update()) else None
    # The update runs beside the loop, never in it: installing took 70 s on
    # hpo, and a reply that began meanwhile went unpaused until halfway
    # through (David, 28 Nov 2026: "It didn't pause for this reply until
    # about halfway through"). Its result is a box the loop reads.
    upd: dict = {}
    updater: Optional[threading.Thread] = None

    def _update() -> None:
        try:
            upd["done"] = check_update(rec)
        except Exception as e:  # noqa: BLE001 - an update never ends the loop
            log.warning("update failed: %s", e)
            upd["done"] = False

    while True:
        if next_update is not None and updater is None and clock() >= next_update:
            next_update = clock() + UPDATE_EVERY_S
            upd.clear()
            updater = threading.Thread(target=_update, name="screen-update", daemon=True)
            updater.start()
        if updater is not None and "done" in upd:
            updater = None
            if upd["done"]:
                upd["ready"] = True
        # The new code takes over only while nothing is paused.
        if upd.get("ready") and not d.holding:
            _start_again()
            return 0
        server: Optional[bool]
        try:
            got = ask(rec)
            last_err = None
            on = bool(got.get("on"))
            server = bool(got.get("pause"))
        except ScreenError as e:
            if str(e) != last_err:
                log.warning("%s", e)
                last_err = str(e)
            server = None
        try:
            # The switch governs the mic too: off is off.
            users = mic() if on else []
            if users != last_mic:
                if users:
                    log.info("microphone in use by %s", users)
                else:
                    log.info("microphone free")
                last_mic = users
            d.step(True if users else server)
        except Exception as e:  # noqa: BLE001 - one bad turn never ends the loop
            # Run from a logon task, a crash stays dead until the next sign-in
            # (how hpo's old ducker went quiet): log it and go round again.
            log.warning("turn failed: %r", e)
            _failures.swallowed(e)
        if server is None:
            # Keep asking quickly while something is paused, so it fails open on time.
            wait = POLL_ON_S if (d.holding or on) else POLL_ERR_S
        else:
            wait = POLL_ON_S if on else POLL_OFF_S
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
    """The user unit; `exe` the launcher's command (or, as before it, the
    module's, to which ` run` is added)."""
    cmd = " ".join(exe) if exe[-1].endswith(".py") else " ".join(exe) + " run"
    return f"""[Unit]
Description=Sasonica: pause this computer's media while you talk or a reply is read
After=graphical-session.target network-online.target

[Service]
ExecStart={cmd}
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


_TASK_SETTINGS = (
    "$s = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) "
    "-AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable "
    "-RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) "
    "-MultipleInstances IgnoreNew; "
    f"Set-ScheduledTask -TaskName '{TASK}' -Settings $s | Out-Null"
)


def install(dry_run: bool = False) -> str:
    if sys.platform.startswith("linux"):
        unit_dir = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "systemd" / "user"
        text = _linux_unit(_launch_cmd())
        if dry_run:
            return f"would write {unit_dir / UNIT}:\n{text}"
        ensure_launcher()
        unit_dir.mkdir(parents=True, exist_ok=True)
        (unit_dir / UNIT).write_text(text)
        subprocess.run(["systemctl", "--user", "daemon-reload"], check=False)
        subprocess.run(["systemctl", "--user", "enable", "--now", UNIT], check=False)
        return f"installed and started {UNIT}"
    if os.name == "nt":
        cmd = subprocess.list2cmdline(_launch_cmd(windowless=True))
        create = ["schtasks", "/create", "/tn", TASK, "/tr", cmd, "/sc", "onlogon",
                  "/it", "/rl", "limited", "/f"]
        if dry_run:
            return "would run: " + subprocess.list2cmdline(create)
        ensure_launcher()
        subprocess.run(create, check=True)
        # schtasks leaves Windows' defaults: stopped after 72 hours, never run
        # on battery (a laptop unplugged), never started again after a crash.
        subprocess.run(["powershell", "-NoProfile", "-Command", _TASK_SETTINGS], check=False)
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
    sub.add_parser("update", help="install the paired server's newer helper now, if any")


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
    if a.screen_cmd == "update":
        try:
            done = check_update(conf)
        except (ScreenError, OSError, subprocess.SubprocessError) as e:
            print(f"screen update: {e}", file=sys.stderr)
            return 1
        print(f"updated to {installed_version()}; restart it (media screen install, or the task)"
              if done else f"up to date ({installed_version() or 'unknown'})")
        return 0
    if a.screen_cmd == "status":
        print(f"paired with {conf['url']} as {conf.get('name')!r}; "
              f"helper version {installed_version() or 'unknown'}")
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
    logs.setup(stream=stream, fmt="%(asctime)s " + logs.DEFAULT_FORMAT)
    if not a.once and may_update():
        # An install from before the launcher moves onto it (agent-mail #1449).
        try:
            done = migrate_to_launcher()
            if done:
                log.info("%s", done)
        except Exception as e:  # noqa: BLE001 - never stops the loop starting
            _failures.swallowed(e)
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
