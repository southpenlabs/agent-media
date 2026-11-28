"""`sasonica update` — the newest binary in place of this one.

  1. fetches `SHA256SUMS` from the server-latest release and, when this
     binary is not the newest, the newest one for this machine, checked
     against it; renamed over this file, so running services keep the file
     they started from;
  2. restarts the server (`sasonica-canvas`), and the session holder too when
     no chat is live in it — a restart ends its chats (they resume on their
     next message, but a turn in progress is cut off). With chats live it is
     left on the old version and says so; `--restart-sessiond` restarts it
     anyway;
  3. removes the unpacked copies of older versions (~180 MB each) that no
     running process still uses.

Where there is no systemd --user (the phone, whose services are Termux's),
it replaces the file and says what to restart. SASONICA_BINARY_BASE is where
the release is (tests: a file:// directory).

The binary it replaces is kept beside it as `sasonica.prev`. On Linux and a
Mac the server must come back up (its service active, `/healthz` answering)
within HEALTH_S of the restart; when it does not, the previous binary is put
back, the services restarted again and the alert `release.<host>` raised.

`--auto` is the self-update (agent_media_core/self_update.py; the timer
`sasonica-update`, which `sasonica install` writes): with
MEDIA_SELF_UPDATE on (the binary's default; `check` only looks, `off` does
nothing), the update above runs only at a quiet moment (no chat mid-turn,
no reply being read), else it waits for the next tick. A session holder
left on the old version for its live chats is restarted by a later tick
once none is live. Each tick is written down for `media doctor`.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import platform
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

from sasonica.__main__ import binary_path

BASE = "https://github.com/southpenlabs/agent-media/releases/download/server-latest"
CANVAS, SESSIOND = "sasonica-canvas.service", "sasonica-sessiond.service"
#: How long the restarted server has to answer before the old binary is put back.
HEALTH_S = 45.0


def base() -> str:
    return (os.environ.get("SASONICA_BINARY_BASE") or BASE).rstrip("/")


def asset() -> str:
    arch = {"x86_64": "x86_64", "amd64": "x86_64", "aarch64": "aarch64",
            "arm64": "aarch64"}.get(platform.machine().lower(), "")
    if not arch:
        raise SystemExit(f"sasonica update: no build for {platform.machine()}")
    if os.name == "nt":
        # x64 only: Windows on ARM64 runs it under emulation (David, 16 Oct
        # 2026), and an emulated Python may still say ARM64 (WMI).
        return "sasonica-windows-x86_64.exe"
    return f"sasonica-{'macos' if sys.platform == 'darwin' else 'linux'}-{arch}"


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def fetch(url: str, dest: Path | None = None, timeout: float = 600):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        if dest is None:
            return r.read()
        with open(dest, "wb") as f:
            shutil.copyfileobj(r, f)
    return None


def wanted_sum(sums: str, name: str) -> str:
    for line in sums.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[1].lstrip("*") == name:
            return parts[0]
    return ""


def _executables() -> list[Path]:
    """Every running process's executable: /proc on Linux, `ps` where there
    is none (a Mac, whose `comm` is the full path)."""
    proc = Path("/proc")
    if proc.is_dir():
        out = []
        for d in proc.iterdir():
            if d.name.isdigit():
                try:
                    out.append(Path(os.readlink(d / "exe")))
                except OSError:
                    pass
        return out
    try:
        ps = subprocess.run(["ps", "-axo", "comm="], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return []
    return [Path(line.strip()) for line in ps.stdout.splitlines() if line.strip().startswith("/")]


def in_use(root: Path) -> set[Path]:
    """The version directories under `root` a running process executes from."""
    used = set()
    for exe in _executables():
        try:
            rel = exe.relative_to(root)
        except ValueError:
            continue
        if len(rel.parts) >= 2:
            used.add(root / rel.parts[0] / rel.parts[1])
    return used


def prune(root: Path, keep: set[Path], *, dry_run: bool = False) -> list[Path]:
    """Remove `<root>/<distribution id>/<version>` directories not in `keep`
    and not in use; the removed ones."""
    gone = []
    busy = in_use(root) | {k.resolve() for k in keep}
    for dist in sorted(p for p in root.iterdir() if p.is_dir()) if root.is_dir() else []:
        for ver in sorted(p for p in dist.iterdir() if p.is_dir()):
            if ver.resolve() in busy or ver in busy:
                continue
            gone.append(ver)
            if not dry_run:
                shutil.rmtree(ver, ignore_errors=True)
    return gone


def _systemd() -> bool:
    return shutil.which("systemctl") is not None and subprocess.run(
        ["systemctl", "--user", "show-environment"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0


def _live_chats() -> int:
    """How many chats sessiond holds live; 0 when it cannot be asked."""
    try:
        from agent_media_server.driver.headless import call
    except ImportError:
        return 0
    r = call("list", timeout=5.0)
    return sum(1 for s in r.get("sessions") or [] if s.get("live")) if r.get("ok") else 0


def _restart_tasks(force_sessiond: bool) -> None:
    """Windows's two logon tasks (install.TASKS), ended and run again. Tasks
    an older install registered with /SC ONLOGON are first re-registered from
    task XML (restart on failure), running what they ran."""
    from sasonica.install import TASKS, UNITS, upgrade_task

    for name, (_word, what) in UNITS.items():
        upgrade_task(TASKS[name], what)

    tasks = [TASKS["sasonica-canvas"]]
    live = _live_chats()
    if live and not force_sessiond:
        print(f"  the session holder has {live} live chat(s): left on the old version until "
              "you run `sasonica update --restart-sessiond` (that ends them)")
    else:
        tasks.append(TASKS["sasonica-sessiond"])
    # The opt-in speech player runs from the binary too. Its mpv is not the
    # binary's and keeps playing: the new launcher finds it on the pipe.
    from sasonica.install import SPEECH_TASK
    if subprocess.run(["schtasks", "/Query", "/TN", SPEECH_TASK],
                      capture_output=True).returncode == 0:
        tasks.append(SPEECH_TASK)
    for task in tasks:
        subprocess.run(["schtasks", "/End", "/TN", task], capture_output=True)
        subprocess.run(["schtasks", "/Run", "/TN", task], capture_output=True)
    print("  restarted " + ", ".join(tasks))


def _restart_launchd(force_sessiond: bool) -> None:
    """A Mac's two launchd agents (install.LABELS), the session holder only
    with no chat live in it, as on Linux."""
    from sasonica.install import LABELS

    domain = f"gui/{os.getuid()}"
    labels = [LABELS["sasonica-canvas"]]
    live = _live_chats()
    if live and not force_sessiond:
        print(f"  the session holder has {live} live chat(s): left on the old version until "
              "you run `sasonica update --restart-sessiond` (that ends them)")
    else:
        labels.append(LABELS["sasonica-sessiond"])
    from sasonica.install import LAUNCH_DAEMONS
    for label in labels:
        # Installed --signed-out: a daemon in the system domain (sudo asks).
        if (LAUNCH_DAEMONS / f"{label}.plist").exists():
            subprocess.run(["sudo", "launchctl", "kickstart", "-k", f"system/{label}"])
        else:
            subprocess.run(["launchctl", "kickstart", "-k", f"{domain}/{label}"])
    print("  restarted " + ", ".join(labels))


def pyapp_root() -> Path:
    """Where PyApp unpacks this binary's versions: its platform's data
    directory (the `directories` crate's)."""
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "pyapp" / "sasonica"
    data = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
    return data / "pyapp" / "sasonica"


def _port() -> int:
    """The port the server unit listens on (`--port N` in its ExecStart)."""
    import re

    unit = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") \
        / "systemd" / "user" / CANVAS
    try:
        m = re.search(r"--port\s+(\d+)", unit.read_text())
    except OSError:
        m = None
    return int(m.group(1)) if m else 8781


def _answers(port: int) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/healthz", timeout=3) as r:
            return r.status == 200
    except OSError:
        return False


def healthy(within: float = HEALTH_S, *, systemd: bool = True) -> bool:
    """The server back up after a restart: its unit active (systemd) and
    /healthz answering, within `within` seconds."""
    import time

    port = _port()
    deadline = time.monotonic() + within
    while True:
        active = not systemd or subprocess.run(
            ["systemctl", "--user", "is-active", "--quiet", CANVAS]).returncode == 0
        if active and _answers(port):
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(1.0)


def _restart_systemd(force_sessiond: bool) -> bool:
    """Restart the server, and the session holder when no chat is live in it
    (or `force_sessiond`). Whether the session holder was left behind."""
    units = [CANVAS]
    live = _live_chats()
    left = bool(live and not force_sessiond)
    if left:
        print(f"  the session holder has {live} live chat(s): left on the old version until "
              "none is (the self-update restarts it then), or `sasonica update "
              "--restart-sessiond` now (that ends them)")
    else:
        units.append(SESSIOND)
    subprocess.run(["systemctl", "--user", "restart", *units])
    print("  restarted " + ", ".join(u.removesuffix(".service") for u in units))
    return left


def _alert(level: str, title: str = "", detail: str = "", fix: str = "") -> None:
    try:
        from agent_media_core import restart_services as rs
    except ImportError:
        return
    rs.alert(level, title, detail, fix, name="release")


def _record(**kw) -> None:
    try:
        from agent_media_core import self_update as su
    except ImportError:
        return
    su.record(kind="binary", **kw)


def _mode() -> str:
    try:
        from agent_media_core import self_update as su
    except ImportError:
        return "on"
    return su.mode("binary")


def _not_quiet() -> list[str]:
    try:
        from agent_media_core import self_update as su
    except ImportError:
        return []
    return su.not_quiet(kind="binary")


def _mail(mode: str, why: list[str]) -> dict:
    """agent-mail's checkout, kept current on the same tick (self_update)."""
    try:
        import time

        from agent_media_core import self_update as su
    except ImportError:
        return {}
    rec = su.mail_record(su.read_state(), su.agent_mail_step(pull=mode == "on" and not why),
                         time.time())
    return {"agent_mail": rec} if rec else {}


def _sessiond_catch_up(mode: str) -> None:
    """A session holder an earlier update left on the old version, restarted
    once no chat is live in it and the moment is quiet."""
    try:
        from agent_media_core import self_update as su
    except ImportError:
        return
    if not su.read_state().get("sessiond_behind") or mode != "on" or not _systemd():
        return
    if _live_chats() or _not_quiet():
        return
    subprocess.run(["systemctl", "--user", "restart", SESSIOND])
    print("  restarted sasonica-sessiond (left on the old version by the last update)")
    st = su.read_state()
    st["sessiond_behind"] = False
    su._write(st)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="sasonica update", description=__doc__.split("\n\n")[0])
    ap.add_argument("--binary", help="the binary to replace (default: this one)")
    ap.add_argument("--restart-sessiond", action="store_true",
                    help="restart the session holder even with chats live (ends them)")
    ap.add_argument("--check", action="store_true", help="only say whether there is a newer one")
    ap.add_argument("--auto", action="store_true",
                    help="the self-update timer's run: update only at a quiet moment, "
                         "as MEDIA_SELF_UPDATE allows (on unless set), and write it down "
                         "for media doctor")
    a = ap.parse_args(argv)
    sys.stdout.reconfigure(line_buffering=True)

    mode = _mode() if a.auto else "on"
    if a.auto and mode == "off":
        _record(mode_=mode, result="off")
        print("self-update is off (MEDIA_SELF_UPDATE=off)")
        return 0
    binary = a.binary or binary_path()
    if not binary:
        print("sasonica update: not running from the binary (a checkout updates with git pull)",
              file=sys.stderr)
        return 2
    binary_p = Path(binary).resolve()
    name = asset()
    try:
        want = wanted_sum(fetch(f"{base()}/SHA256SUMS", timeout=30).decode(), name)
    except OSError as e:
        print(f"sasonica update: could not reach the release ({e})", file=sys.stderr)
        if a.auto:
            _record(mode_=mode, have=sha256(binary_p), reached=False, error=str(e)[:300],
                    result="offline")
            return 0
        return 1
    if not want:
        print(f"sasonica update: the release has no {name}", file=sys.stderr)
        if a.auto:
            _record(mode_=mode, have=sha256(binary_p), reached=False,
                    error=f"the release has no {name}", result="offline")
            return 0
        return 1
    have = sha256(binary_p)
    why = _not_quiet() if a.auto else []
    extra = _mail(mode, why) if a.auto else {}
    if have == want:
        print("sasonica is up to date")
        if a.auto:
            _record(mode_=mode, have=have, latest=want, result="up-to-date", **extra)
            _sessiond_catch_up(mode)
        return 0
    if a.check or (a.auto and mode == "check"):
        print("a newer sasonica is available: sasonica update")
        if a.auto:
            _record(mode_=mode, have=have, latest=want, result="behind", **extra)
        return 0
    if why:
        print("a newer sasonica is available, but not now: " + "; ".join(why))
        _record(mode_=mode, have=have, latest=want, result="busy", why=why, **extra)
        return 0

    new = binary_p.with_name(binary_p.name + ".new")
    prev = binary_p.with_name(binary_p.name + ".prev")
    print(f"== Downloading {name}")
    try:
        fetch(f"{base()}/{name}", new)
    except OSError as e:
        new.unlink(missing_ok=True)
        print(f"sasonica update: download failed ({e})", file=sys.stderr)
        if a.auto:
            _record(mode_=mode, have=have, latest=want, result="failed",
                    error=f"download failed: {e}"[:300], **extra)
        return 1
    if sha256(new) != want:
        new.unlink(missing_ok=True)
        print("sasonica update: the download does not match its checksum; nothing changed",
              file=sys.stderr)
        if a.auto:
            _record(mode_=mode, have=have, latest=want, result="failed",
                    error="the download did not match its checksum", **extra)
        return 1
    new.chmod(0o755)
    if os.name == "nt":
        # A running .exe cannot be replaced, but it can be renamed: the old
        # one steps aside (removed next time, when nothing runs it).
        old = binary_p.with_name(binary_p.name + ".old")
        try:
            old.unlink(missing_ok=True)
        except OSError:
            pass
        os.replace(binary_p, old)
    else:
        # The one being replaced, kept to step back to: a hard link (the same
        # file, no copy), else a copy.
        prev.unlink(missing_ok=True)
        try:
            os.link(binary_p, prev)
        except OSError:
            shutil.copy2(binary_p, prev)
    os.replace(new, binary_p)
    # Unpacked now, so the restart below starts at once.
    try:
        out = subprocess.run([str(binary_p), "version"], capture_output=True, text=True).stdout
    except OSError as e:             # not runnable here: say so, it is installed
        out = f"installed (it did not start: {e})"
    print(f"  now {out.strip() or 'installed'}")

    print("== Services")
    if os.name == "nt":
        _restart_tasks(a.restart_sessiond)
        # Unpacked versions stay: Windows gives no simple way to see which a
        # running process uses. They are under %LOCALAPPDATA%\pyapp\data.
        if a.auto:
            _record(mode_=mode, have=want, latest=want, result="updated", **extra)
        return 0
    left = False
    if sys.platform == "darwin":
        _restart_launchd(a.restart_sessiond)
        ok = healthy(systemd=False)
    elif not _systemd():
        print("  no systemd --user here: restart the server and the session holder yourself "
              "(on the phone: sv restart sasonica-canvas sasonica-sessiond, in Termux)")
        if a.auto:
            _record(mode_=mode, have=want, latest=want, result="updated", **extra)
        return 0
    else:
        left = _restart_systemd(a.restart_sessiond)
        ok = healthy()
    if not ok:
        print(f"  the server did not come back within {HEALTH_S:.0f} s: stepping back to the "
              "previous binary")
        os.replace(prev, binary_p)
        if sys.platform == "darwin":
            _restart_launchd(True)
        else:
            subprocess.run(["systemctl", "--user", "restart", CANVAS, SESSIOND])
        back = healthy(systemd=sys.platform != "darwin")
        import socket

        host = socket.gethostname().split(".")[0]
        _alert("warn", f"sasonica update failed on {host}; stepped back",
               "The new binary's server did not answer /healthz after its restart; the "
               "previous binary runs again" + ("" if back else ", but the server is STILL down"),
               "journalctl --user -u sasonica-canvas -e")
        print("  stepped back" + ("" if back else ", and the server is STILL not answering"))
        if a.auto:
            _record(mode_=mode, have=have, latest=want, result="rolled-back",
                    error="the server did not answer after the restart", **extra)
        return 1
    _alert("ok")
    if a.auto:
        _record(mode_=mode, have=want, latest=want, result="updated", sessiond_behind=left,
                **extra)

    print("== Old versions")
    # The newest unpacked is the one just installed, whatever runs it yet.
    root = pyapp_root()
    vers = [v for d in root.iterdir() if d.is_dir() for v in d.iterdir() if v.is_dir()] \
        if root.is_dir() else []
    gone = prune(root, {max(vers, key=lambda v: v.stat().st_mtime)} if vers else set())
    print(f"  removed {len(gone)}" if gone else "  none to remove")
    return 0
