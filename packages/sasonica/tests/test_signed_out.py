"""Kept running when nobody is logged in (David, 29 Oct 2026: "mention linger
with the installation process if the computer won't be left logged in … is
there a Windows equivalent? … or Mac?"): Linux's linger checked and turned on,
Windows' tasks at boot as S4U and a Mac's launchd daemons with --signed-out,
remembered. Every command is recorded, none run."""

from __future__ import annotations

import argparse
import plistlib
import subprocess
import xml.etree.ElementTree as ET

from sasonica import install

NS = {"t": "http://schemas.microsoft.com/windows/2004/02/mit/task"}


def _parsed(xml):
    return ET.fromstring(xml.split("\n", 1)[1])


def test_a_signed_out_task_starts_at_boot_as_s4u():
    t = _parsed(install.task_xml('--headless "s.exe" serve', "PC\\david", "canvas", signed_out=True))
    assert t.find("t:Triggers/t:BootTrigger", NS) is not None
    assert t.find("t:Triggers/t:LogonTrigger", NS) is None
    assert t.find("t:Principals/t:Principal/t:LogonType", NS).text == "S4U"
    assert t.find("t:Principals/t:Principal/t:RunLevel", NS).text == "LeastPrivilege"
    plain = _parsed(install.task_xml('--headless "s.exe" serve', "PC\\david", "canvas"))
    assert plain.find("t:Triggers/t:LogonTrigger", NS) is not None
    assert plain.find("t:Principals/t:Principal/t:LogonType", NS).text == "InteractiveToken"


def test_windows_remembers_the_choice(monkeypatch):
    s4u = install.task_xml("x", "u", "d", signed_out=True)
    monkeypatch.setattr(install, "_task_definition", lambda name: s4u)
    assert install._win_signed_out(argparse.Namespace(signed_out=None)) is True
    assert install._win_signed_out(argparse.Namespace(signed_out="off")) is False
    monkeypatch.setattr(install, "_task_definition", lambda name: "")
    assert install._win_signed_out(argparse.Namespace(signed_out=None)) is False
    assert install._win_signed_out(argparse.Namespace(signed_out="on")) is True


def test_windows_says_how_when_task_scheduler_refuses(monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(install, "register_task", lambda name, xml, dry_run: 1)
    monkeypatch.setattr(install, "_run", lambda argv, dry_run: calls.append(argv) or 0)
    a = argparse.Namespace(bind="0.0.0.0", port=8781, dry_run=False, signed_out="on")
    assert install._schtasks("C:\\s.exe", a) == 1
    out = capsys.readouterr().out
    assert "Run as administrator" in out and "--signed-out off" in out


def test_a_mac_daemon_runs_as_the_user():
    d = plistlib.loads(install.daemon_plist_text("com.sasonica.canvas", "/opt/s", "serve",
                                                  "0.0.0.0", 8781, "david").encode())
    assert d["UserName"] == "david" and d["RunAtLoad"] and d["KeepAlive"]
    assert d["ProgramArguments"] == ["/opt/s", "serve", "--bind", "0.0.0.0", "--port", "8781"]
    assert d["EnvironmentVariables"]["HOME"]


def test_a_mac_signed_out_install_uses_sudo_and_the_system_domain(monkeypatch, tmp_path, capsys):
    calls = []
    monkeypatch.setattr(install, "_run", lambda argv, dry_run: calls.append(argv) or 0)
    monkeypatch.setattr(install, "LAUNCH_DAEMONS", tmp_path / "LaunchDaemons")
    monkeypatch.setattr(install, "launch_agents", lambda: tmp_path / "LaunchAgents")
    a = argparse.Namespace(bind="0.0.0.0", port=8781, dry_run=True, signed_out="on",
                           units=dict(install.UNITS))
    assert install._launchd("/opt/s", a) == 0
    assert ["sudo", "launchctl", "bootstrap", "system",
            str(tmp_path / "LaunchDaemons" / "com.sasonica.canvas.plist")] in calls
    assert not any(c[:2] == ["launchctl", "bootstrap"] for c in calls)
    assert "FileVault" in capsys.readouterr().out


def _loginctl(monkeypatch, states):
    """loginctl answering Linger from `states` in turn; the commands kept."""
    calls = []

    def run(argv, **kw):
        calls.append(argv)
        out = states.pop(0) if argv[1] == "show-user" else ""
        return subprocess.CompletedProcess(argv, 0, stdout=out + "\n", stderr="")
    monkeypatch.setattr(install.shutil, "which", lambda name, **kw: "/usr/bin/" + name)
    monkeypatch.setattr(install.subprocess, "run", run)
    return calls


def test_linger_already_on_is_left(monkeypatch, capsys):
    calls = _loginctl(monkeypatch, ["yes"])
    install._linger_step(argparse.Namespace(dry_run=False))
    assert not any(c[1] == "enable-linger" for c in calls)
    assert "linger is on" in capsys.readouterr().out


def test_linger_off_is_turned_on(monkeypatch, capsys):
    calls = _loginctl(monkeypatch, ["no", "yes"])
    install._linger_step(argparse.Namespace(dry_run=False))
    assert any(c[1] == "enable-linger" and "--no-ask-password" in c for c in calls)
    assert "linger is on now" in capsys.readouterr().out


def test_linger_refused_says_the_sudo_line(monkeypatch, capsys):
    _loginctl(monkeypatch, ["no", "no"])
    install._linger_step(argparse.Namespace(dry_run=False))
    assert "sudo loginctl enable-linger" in capsys.readouterr().out
