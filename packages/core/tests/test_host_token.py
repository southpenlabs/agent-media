"""The host's own key (host_token): who wins, and what ensure() writes."""

import stat

import pytest

from agent_media_core import host_token


@pytest.fixture
def home(tmp_path, monkeypatch):
    for name in ("MEDIA_HOST_TOKEN", "AMUX_AUTH_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / ".config"))
    return tmp_path


def _amux(home, tok):
    (home / ".amux").mkdir()
    (home / ".amux" / "auth_token").write_text(tok + "\n")


def test_no_key_anywhere_is_empty(home):
    assert host_token.read() == ""


def test_ensure_makes_a_private_key_and_keeps_it(home):
    tok = host_token.ensure()
    assert len(tok) >= 40
    p = home / ".config" / "agent-media" / "host_token"
    assert p.read_text().strip() == tok
    assert stat.S_IMODE(p.stat().st_mode) == 0o600
    assert host_token.ensure() == tok
    assert host_token.read() == tok


def test_ensure_adopts_the_amux_token(home):
    _amux(home, "amuxtok")
    assert host_token.ensure() == "amuxtok"
    assert (home / ".config" / "agent-media" / "host_token").read_text().strip() == "amuxtok"


def test_amux_file_still_read_before_ensure(home):
    _amux(home, "amuxtok")
    assert host_token.read() == "amuxtok"


def test_own_file_wins_over_amux(home):
    _amux(home, "amuxtok")
    p = home / ".config" / "agent-media" / "host_token"
    p.parent.mkdir(parents=True)
    p.write_text("mine\n")
    assert host_token.read() == "mine"


def test_env_wins_and_none_means_no_key(home, monkeypatch):
    _amux(home, "amuxtok")
    monkeypatch.setenv("AMUX_AUTH_TOKEN", "envtok")
    assert host_token.read() == "envtok"
    monkeypatch.setenv("MEDIA_HOST_TOKEN", "hostenv")
    assert host_token.read() == "hostenv"
    monkeypatch.setenv("MEDIA_HOST_TOKEN", "none")
    assert host_token.read() == ""
    assert host_token.ensure() == ""
    assert not (home / ".config" / "agent-media" / "host_token").exists()
