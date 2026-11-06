"""procinfo: other processes read one way on every platform."""

import re
import subprocess
import sys
from pathlib import Path

from agent_media_core import procinfo


def test_alive():
    import os

    assert procinfo.alive(os.getpid())
    p = subprocess.Popen([sys.executable, "-c", "pass"])
    p.wait()
    assert not procinfo.alive(p.pid)
    assert not procinfo.alive(0) and not procinfo.alive(None) and not procinfo.alive("x")


def test_nothing_else_probes_a_pid_with_signal_0():
    """`os.kill(pid, 0)` is Ctrl+C to the whole console on Windows (CI's run
    ended in a KeyboardInterrupt): liveness goes through procinfo.alive."""
    root = Path(procinfo.__file__).resolve().parents[2]      # packages/core
    found = []
    for pkg in ("core", "server", "visual", "sasonica"):
        for f in (root.parent / pkg / "src").rglob("*.py"):
            if f.name == "procinfo.py":
                continue
            for n, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
                if re.search(r"\bkill\([^,()]+(\([^)]*\))?,\s*0\)", line):
                    found.append(f"{f.name}:{n}")
    assert not found, found


# --- outliving_service: a replay started by a service outlives its restart ---

def _runtime(tmp_path, monkeypatch, *, bus=True):
    rt = tmp_path / "run"
    (rt / "systemd").mkdir(parents=True)
    if bus:
        (rt / "systemd" / "private").write_text("")
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(rt))


def test_outliving_service_puts_a_service_child_in_a_scope(tmp_path, monkeypatch):
    """From inside a systemd unit, with a user manager to ask: run in a scope
    of its own, so a restart of the unit (the canvas) does not kill it."""
    _runtime(tmp_path, monkeypatch)
    monkeypatch.setenv("INVOCATION_ID", "abc")
    monkeypatch.delenv("MEDIA_OWN_SCOPE", raising=False)
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/systemd-run" if name == "systemd-run" else None)
    cmd = ["python3", "-m", "agent_media_core.cli", "replay-track"]
    assert procinfo.outliving_service(cmd) == [
        "/usr/bin/systemd-run", "--user", "--scope", "--quiet", "--collect", "--", *cmd]


def test_outliving_service_leaves_it_alone_elsewhere(tmp_path, monkeypatch):
    """Not in a unit, no systemd-run, no user manager, or turned off: the
    command as it is."""
    cmd = ["python3", "-c", "pass"]
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/systemd-run")
    _runtime(tmp_path, monkeypatch)
    monkeypatch.delenv("INVOCATION_ID", raising=False)
    assert procinfo.outliving_service(cmd) == cmd
    monkeypatch.setenv("INVOCATION_ID", "abc")
    monkeypatch.setenv("MEDIA_OWN_SCOPE", "0")
    assert procinfo.outliving_service(cmd) == cmd
    monkeypatch.delenv("MEDIA_OWN_SCOPE")
    monkeypatch.setattr("shutil.which", lambda name: None)
    assert procinfo.outliving_service(cmd) == cmd
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/systemd-run")
    _runtime(tmp_path / "nobus", monkeypatch, bus=False)
    assert procinfo.outliving_service(cmd) == cmd
