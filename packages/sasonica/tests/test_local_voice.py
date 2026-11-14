"""`sasonica install --local-voice`: Kokoro through `media-setup voices`.

Nothing is run: the command line the installer would run is recorded.
"""
from __future__ import annotations

import argparse

import pytest

from sasonica import install


def test_the_flag_takes_kokoro_a_voice_or_off():
    assert install.local_voice_arg("kokoro") == "kokoro:af_alloy"
    assert install.local_voice_arg("kokoro:bf_emma") == "kokoro:bf_emma"
    assert install.local_voice_arg("off") == "off"
    for bad in ("piper:en_GB-cori-high", "espeak", "kokoro:Heart"):
        with pytest.raises(argparse.ArgumentTypeError):
            install.local_voice_arg(bad)


@pytest.fixture
def ran(monkeypatch):
    calls = []

    def run(argv, *, dry_run):
        calls.append((argv, dry_run))
        return 0
    monkeypatch.setattr(install, "_run", run)
    return calls


def _a(value, dry_run=False):
    return argparse.Namespace(local_voice=value, dry_run=dry_run)


def test_linux_runs_media_setup_voices_with_the_fallback(ran, monkeypatch):
    monkeypatch.setattr(install.sys, "platform", "linux")
    monkeypatch.setattr(install, "_windows", lambda: False)
    install._local_voice(["/opt/sasonica"], _a("kokoro:bf_emma"))
    install._local_voice(["/opt/sasonica"], _a("kokoro:af_alloy", dry_run=True))
    install._local_voice(["/opt/sasonica"], _a("off"))
    assert ran == [
        (["/opt/sasonica", "media-setup", "voices", "kokoro:bf_emma", "--fallback"], False),
        (["/opt/sasonica", "media-setup", "voices", "kokoro:af_alloy", "--fallback",
          "--dry-run"], False),
        (["/opt/sasonica", "media-setup", "voices", "--remove", "kokoro"], False),
    ]


@pytest.mark.parametrize("platform,win", [("darwin", False), ("win32", True)])
def test_mac_and_windows_are_told_and_nothing_runs(ran, monkeypatch, capsys, platform, win):
    monkeypatch.setattr(install.sys, "platform", platform)
    monkeypatch.setattr(install, "_windows", lambda: win)
    assert install._local_voice(["sasonica"], _a("kokoro:af_alloy")) == 0
    assert ran == []
    assert "Linux only for now" in capsys.readouterr().out
