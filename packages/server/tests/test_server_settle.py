"""`media server settle` (server_settle.py): after a restore on the machine a
server moved to, the state is that machine's. All under the test's own
state dir; "this machine" is a patched hostname.
"""

from __future__ import annotations

import json

import pytest

from agent_media_server import server_settle as ss, sessiond_remote


@pytest.fixture
def there(tmp_path, monkeypatch):
    monkeypatch.setattr(sessiond_remote.socket, "gethostname", lambda: "red2")
    env = tmp_path / "agent-media.env"
    env.write_text("MEDIA_HEADLESS=1\nMEDIA_SESSIOND_DEFAULT_HOST=red2\n")
    monkeypatch.setenv("MEDIA_ENV_FILE", str(env))
    sd = ss._state() / "sessiond"
    sd.mkdir(parents=True)
    return sd, env, tmp_path


def _rec(sd, sid, **kw):
    (sd / f"{sid}.json").write_text(json.dumps({"session": sid, "state": "ended", **kw}))


def _read(sd, sid):
    return json.loads((sd / f"{sid}.json").read_text())


def test_a_chat_that_ran_here_gets_this_machines_own_record_back(there):
    sd, _env, tmp = there
    # Before the restore: this machine's records, one of them the old
    # server never knew.
    _rec(sd, "worker-chat", state="waiting", cwd=str(tmp), turns=7, pid=42)
    _rec(sd, "only-here", cwd=str(tmp), turns=1)
    own = tmp / "own"
    assert ss.stash(own)["stashed"] == 2
    # The restore: the old server's view of the first, and a chat of its own.
    (sd / "only-here.json").unlink()
    _rec(sd, "worker-chat", host="red2", cwd="/home/old/x", remote_cwd=str(tmp), seq_seen=3)
    _rec(sd, "old-chat", cwd=str(tmp))
    (sd / "remote").mkdir()
    (sd / "remote" / "worker-chat.jsonl").write_text("{}\n")
    got = ss.settle(own)
    mine = _read(sd, "worker-chat")
    assert "host" not in mine and mine["turns"] == 7 and mine["cwd"] == str(tmp)
    assert not (sd / "remote" / "worker-chat.jsonl").exists()
    assert _read(sd, "only-here")["turns"] == 1
    assert got["adopted"] == 1 and got["kept_own"] == 1 and got["adopted_without_own"] == 0


def test_a_worker_chat_with_no_record_here_stays_as_an_ended_thread(there):
    sd, _env, tmp = there
    _rec(sd, "lost", host="red2", state="working", cwd=str(tmp))
    _rec(sd, "elsewhere", host="red9", state="working")
    got = ss.settle(tmp / "nothing-stashed")
    assert "host" not in _read(sd, "lost") and _read(sd, "lost")["state"] == "ended"
    assert _read(sd, "elsewhere")["host"] == "red9"          # another host's: left alone
    assert got["adopted_without_own"] == 1


def test_nothing_the_stop_cut_off_is_resumed_and_it_is_listed(there):
    sd, _env, tmp = there
    _rec(sd, "cut", state="ended", cwd=str(tmp), first_text="fix the  thing",
         cut_off={"why": "restart", "at": 1.0}, held=[{"uuid": "u", "text": "also this"}])
    _rec(sd, "idle", state="waiting", cwd=str(tmp))
    _rec(sd, "done", state="ended", cwd=str(tmp))
    (sd / "sessiond.stopped.json").write_text('{"at": 1}')
    got = ss.settle(None)
    assert _read(sd, "cut")["cut_off"] is None and _read(sd, "cut")["held"]   # its message stays
    assert _read(sd, "idle")["state"] == "ended"
    assert [c["session"] for c in got["not_resumed"]] == ["cut"]
    assert got["not_resumed"][0]["first_text"] == "fix the thing"
    assert json.loads((ss._state() / "moved-chats.json").read_text())["not_resumed"]
    assert not (sd / "sessiond.stopped.json").exists()
    # The real session host agrees: nothing is due.
    from agent_media_server import sessiond

    sup = sessiond.Supervisor(root=sd)
    sup._kill_orphans = lambda ours: None
    sup.load()
    assert not any(s.cut_off or s.live for s in sup.sessions.values())


def test_folders_not_on_this_machine_are_counted(there):
    sd, _env, tmp = there
    for i in range(3):
        _rec(sd, f"a{i}", cwd="/home/old/scratch")
    _rec(sd, "b", cwd=str(tmp))
    got = ss.settle(None)
    assert got["folders_missing"] == [{"cwd": "/home/old/scratch", "chats": 3}]


def test_a_default_host_naming_this_machine_is_unset(there):
    _sd, env, _tmp = there
    got = ss.settle(None)
    text = env.read_text()
    assert got["default_host_unset"] and "MEDIA_HEADLESS=1\n" in text
    assert not any(ln.startswith("MEDIA_SESSIOND_DEFAULT_HOST") for ln in text.splitlines())


def test_a_default_host_naming_another_machine_stays(there, monkeypatch):
    _sd, env, _tmp = there
    env.write_text("MEDIA_SESSIOND_DEFAULT_HOST=red7\n")
    assert not ss.settle(None)["default_host_unset"]
    assert env.read_text() == "MEDIA_SESSIOND_DEFAULT_HOST=red7\n"


def test_carried_units_are_written_where_missing_and_enabled(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    from agent_media_server.server_move import units_dir

    dst = units_dir()
    dst.mkdir(parents=True)
    (dst / "agent-media-sessiond.service").write_text("the release's own\n")
    src = tmp_path / "units"
    src.mkdir()
    (src / "agent-media-sessiond.service").write_text("the old machine's\n")
    (src / "agent-media-visual-canvas.service").write_text("canvas\n")
    (src / "agent-media-gone.service").write_text("x\n")
    (src / "enabled.json").write_text(json.dumps(
        ["agent-media-visual-canvas.service", "agent-media-sessiond.service", "not-carried.service"]))
    ran: list[list[str]] = []
    got = ss.install_units(src, run=lambda argv: (ran.append(argv), (0, ""))[1])
    assert (dst / "agent-media-sessiond.service").read_text() == "the release's own\n"
    assert (dst / "agent-media-visual-canvas.service").read_text() == "canvas\n"
    assert got["kept"] == ["agent-media-sessiond.service"]
    assert sorted(got["written"]) == ["agent-media-gone.service", "agent-media-visual-canvas.service"]
    assert got["enabled"] == ["agent-media-visual-canvas.service", "agent-media-sessiond.service"]
    assert ran[0] == ["systemctl", "--user", "daemon-reload"]


def test_this_machines_own_settings_survive_the_restore(there):
    sd, env, tmp = there
    env.write_text("MEDIA_SESSIOND_CLAUDE=/home/x/fnm/bin/claude\nMEDIA_HEADLESS=1\n")
    own = tmp / "own"
    ss.stash(own)
    # The restore: the old machine's env file, without that key.
    env.write_text("MEDIA_PUBLIC_URL=https://old.example\n"
                   "MEDIA_BACKUP_DEST=/mnt/old-machine/backups\n"
                   "MEDIA_BACKUP_REPO=sftp:red2:/home/x/restic\n")
    got = ss.settle(own)
    assert got["host_keys_kept"] == ["MEDIA_SESSIOND_CLAUDE"]
    assert "MEDIA_SESSIOND_CLAUDE=/home/x/fnm/bin/claude\n" in env.read_text()
    assert len(got["warnings"]) == 2 and "nowhere to go" in got["warnings"][0]
    assert "beside the data" in got["warnings"][1]
    # Already in the restored file: not written twice.
    assert ss.keep_host_keys(own) == []


# --- the listeners' addresses ---------------------------------------------------------------

OLD_TS, NEW_TS = "100.103.43.93", "100.65.209.67"


_move_binds = ss.move_binds


def _binds(lines, has=(NEW_TS,), tailnet=NEW_TS):
    return _move_binds(lines, bindable=lambda ip: ip in has, tailnet=lambda: tailnet)


def test_the_old_machines_tailnet_bind_becomes_this_machines():
    """The cutover, 13 Nov 2026: `MEDIA_SHARE_BIND` came across as red5's
    tailnet address and the share listener crash-looped on red2."""
    lines = ["MEDIA_HEADLESS=1", f"MEDIA_SHARE_BIND={OLD_TS}", f"export MEDIA_FEED_BIND='{OLD_TS}'",
             "MEDIA_SPEECH_STATE_BIND=127.0.0.1", "MEDIA_VISUAL_BIND=0.0.0.0",
             f"MEDIA_SHIM_BIND={NEW_TS}", "MEDIA_COMPLETIONS_BIND=red2"]
    moved, warnings = _binds(lines)
    assert [(b["key"], b["was"], b["now"]) for b in moved] == [
        ("MEDIA_SHARE_BIND", OLD_TS, NEW_TS), ("MEDIA_FEED_BIND", OLD_TS, NEW_TS)]
    assert not warnings
    assert f"MEDIA_SHARE_BIND={NEW_TS}" in lines and f"export MEDIA_FEED_BIND={NEW_TS}" in lines
    assert sum(OLD_TS in ln and ln.startswith("#") for ln in lines) == 2
    # Loopback, a wildcard, an address it has and a name are left as they are.
    for kept in ("MEDIA_SPEECH_STATE_BIND=127.0.0.1", "MEDIA_VISUAL_BIND=0.0.0.0",
                 f"MEDIA_SHIM_BIND={NEW_TS}", "MEDIA_COMPLETIONS_BIND=red2"):
        assert kept in lines
    # A second settle finds nothing to do.
    assert _binds(lines) == ([], [])


def test_a_bind_it_cannot_move_is_said_not_guessed():
    # No tailnet address here, and a LAN address of the old machine's.
    lines = [f"MEDIA_SHARE_BIND={OLD_TS}", "MEDIA_FEED_BIND=192.168.7.9"]
    before = list(lines)
    moved, warnings = _binds(lines, has=(), tailnet="")
    assert not moved and lines == before
    assert len(warnings) == 2 and all("not an address of this machine" in w for w in warnings)


def test_a_setting_built_on_the_old_address_is_named():
    lines = [f"MEDIA_FEED_BIND={OLD_TS}", f"MEDIA_FEED_BASE_URL=http://{OLD_TS}:8770"]
    moved, warnings = _binds(lines)
    assert len(moved) == 1
    assert warnings == [f"MEDIA_FEED_BASE_URL still names {OLD_TS}, the old machine's address"]


def test_settle_writes_the_moved_bind_into_the_env_file(there, monkeypatch):
    _sd, env, _tmp = there
    env.write_text(env.read_text() + f"MEDIA_SHARE_BIND={OLD_TS}\n")
    # This machine's addresses are the test's, not the one it runs on.
    monkeypatch.setattr(ss, "move_binds", _binds)
    got = ss.settle()
    assert got["binds_moved"] == [{"key": "MEDIA_SHARE_BIND", "was": OLD_TS, "now": NEW_TS}]
    assert f"MEDIA_SHARE_BIND={NEW_TS}" in env.read_text().splitlines()
