"""MEDIA_ENV_ONLY=1: a server with a settings file of its own reads that file
and the shipped defaults, never the machine's agent-media.env (red5's small
server, 14 Nov 2026)."""

import os

from agent_media_core.intake import _env
from agent_media_core.intake._env import load_env_file


def _homes(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / ".config").mkdir(parents=True)
    (home / ".config" / "agent-media.env").write_text("MEDIA_ONLY_PROBE_BIG=machine\nMEDIA_ONLY_PROBE_BOTH=machine\n")
    own = tmp_path / "server.env"
    own.write_text("MEDIA_ONLY_PROBE_BOTH=own\n")
    monkeypatch.setattr(_env.Path, "home", lambda: home)
    for k in ("MEDIA_ONLY_PROBE_BIG", "MEDIA_ONLY_PROBE_BOTH", "RELAY_ENV_FILE"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("MEDIA_ENV_FILE", str(own))
    return own


def test_env_only_skips_the_machines_file(tmp_path, monkeypatch):
    _homes(tmp_path, monkeypatch)
    monkeypatch.setenv("MEDIA_ENV_ONLY", "1")
    load_env_file("test")
    assert os.environ["MEDIA_ONLY_PROBE_BOTH"] == "own"
    assert "MEDIA_ONLY_PROBE_BIG" not in os.environ


def test_without_it_the_machines_file_fills_the_rest(tmp_path, monkeypatch):
    _homes(tmp_path, monkeypatch)
    monkeypatch.delenv("MEDIA_ENV_ONLY", raising=False)
    load_env_file("test")
    assert os.environ["MEDIA_ONLY_PROBE_BOTH"] == "own"
    assert os.environ["MEDIA_ONLY_PROBE_BIG"] == "machine"
