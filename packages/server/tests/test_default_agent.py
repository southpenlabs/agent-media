"""The default agent (default_agent.py): what a start that names none gets
(David, 15 Nov 2026, agent-mail #521, #522)."""

from __future__ import annotations

import pytest

from agent_media_server import auth, default_agent as da


@pytest.fixture()
def here(monkeypatch):
    """Which agents are installed: the set the test puts in."""
    have: set = set()
    monkeypatch.setattr(da, "_installed", lambda agent: agent in have)
    monkeypatch.delenv("MEDIA_ASK_AGENT", raising=False)
    return have


def test_with_none_set_opencode_then_claude(here):
    assert da.resolve() == ("claude", "none")          # nothing installed
    here.add("claude")
    assert da.resolve() == ("claude", "fallback")
    here.add("opencode")
    assert da.resolve() == ("opencode", "fallback")    # free models, no key


def test_the_servers_setting_wins_and_passes_over_one_not_installed(here):
    here.update({"claude", "opencode"})
    da.set_default("claude")
    assert da.resolve() == ("claude", "server")
    da.set_default("codex")
    assert da.resolve() == ("opencode", "fallback")    # codex isn't here
    here.add("codex")
    assert da.resolve() == ("codex", "server")
    # A headless start can't run codex: the next that can.
    assert da.resolve(headless=True) == ("opencode", "fallback")
    da.set_default("")
    assert da.server_default() == ""


def test_the_env_files_older_setting_is_below_the_servers(here, monkeypatch):
    here.update({"claude", "opencode", "pi"})
    monkeypatch.setenv("MEDIA_ASK_AGENT", "pi")
    assert da.resolve() == ("pi", "env")
    da.set_default("claude")
    assert da.resolve() == ("claude", "server")


def test_a_folders_default_wins_beneath_it(here, tmp_path):
    here.update({"claude", "opencode"})
    proj = tmp_path / "proj"
    (proj / "sub").mkdir(parents=True)
    da.set_default("claude")
    da.set_default("opencode", str(proj))
    assert da.resolve(str(proj / "sub")) == ("opencode", "folder")
    assert da.resolve(str(tmp_path)) == ("claude", "server")
    da.set_default("", str(proj))
    assert da.folder_defaults() == {} and da.resolve(str(proj)) == ("claude", "server")


def test_settings_reads_for_anyone_paired_and_sets_for_an_admin(here, monkeypatch, tmp_path):
    here.update({"claude", "opencode"})
    users = {"admin": {"username": "d"}, "member": {"username": "m", "device": "x",
                                                    "role": "member"}}
    monkeypatch.setattr(auth, "gate", lambda b: (users[b], None) if b in users
                        else (None, {"status": 401, "error": "no"}))
    ok, d = da.settings("member")
    assert ok and d["agent"] == "" and d["effective"] == "opencode" and d["source"] == "fallback"
    assert {"id": "opencode", "installed": True, "headless": True} in d["choices"]
    ok, d = da.settings("member", {"agent": "claude"})
    assert not ok and d["code"] == "not_admin"
    ok, d = da.settings("admin", {"agent": "claude"})
    assert ok and d["agent"] == "claude" and d["effective"] == "claude"
    ok, d = da.settings("admin", {"agent": "nonsense"})
    assert not ok and d["status"] == 400
    ok, d = da.settings("admin", {"agent": "opencode", "folder": str(tmp_path)})
    assert ok and d["folders"] == {str(tmp_path.resolve()): "opencode"}
    ok, d = da.settings("admin", {"agent": ""})
    assert ok and d["agent"] == ""


def test_media_session_start_uses_it_and_says_so(here, monkeypatch, tmp_path, capsys):
    import json

    from agent_media_server import headless_mail as hm

    here.update({"claude", "opencode"})
    calls = []
    monkeypatch.setattr(hm, "_call", lambda op, **kw: calls.append((op, kw)) or {
        "ok": True, "session": "ses_" + "a" * 26})
    rc, d = hm.start(str(tmp_path), "hi")
    assert rc == hm.RC_SENT and d["agent"] == "opencode" and d["agent_source"] == "fallback"
    assert [kw["agent"] for op, kw in calls if op == "start"] == ["opencode"]
    # Named on another host: opencode runs on session workers too (18 Nov
    # 2026), so the default stays opencode there.
    calls.clear()
    monkeypatch.setattr(hm, "_call", lambda op, **kw: calls.append((op, kw)) or {
        "ok": True, "session": "ses_" + "b" * 26, "host": "w1"})
    rc, d = hm.start(str(tmp_path), "hi", host="w1")
    assert rc == hm.RC_SENT and d.get("agent_source") == "fallback"
    assert [kw["agent"] for op, kw in calls if op == "start"] == ["opencode"]
    # The command shows it and sets it.
    assert hm.main(["default-agent", "claude", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["effective"] == "claude"


def test_sessiond_starts_the_default_when_none_is_named(here, monkeypatch):
    from agent_media_server import sessiond

    here.add("opencode")
    assert sessiond._default_agent("/tmp") == "opencode"
    monkeypatch.setattr(da, "resolve", lambda *a, **k: 1 / 0)
    assert sessiond._default_agent("/tmp") == "claude"
