"""The practice copy's fence (server_sandbox.py): a restored copy started
beside the real server resumes no chat, holds no outside credential and
reads no restored env file. Everything is under tmp_path; no unit is started.
"""

from __future__ import annotations

import json

import pytest

from agent_media_server import backup, server_address, server_move as sm, server_sandbox as sb


@pytest.fixture
def copy(tmp_path, monkeypatch):
    monkeypatch.setenv("MEDIA_SANDBOX_ROOT", str(tmp_path / "sandbox"))
    r = sb.roots()
    sd = r["state"] / "sessiond"
    sd.mkdir(parents=True)
    cfg = r["home"] / ".config" / "agent-media"
    cfg.mkdir(parents=True)

    def rec(sid, **kw):
        (sd / f"{sid}.json").write_text(json.dumps({"session": sid, "state": "ended",
                                                    "cut_off": None, "held": [], "queued": [],
                                                    "pending": [], "pid": None, **kw}))

    rec("working", state="working", pid=4242,
        held=[{"uuid": "u", "text": "do the thing"}], queued=[{"text": "next"}])
    rec("cutoff", state="ended", cut_off={"why": "restart", "due": True})
    rec("quiet")
    rec("elsewhere", host="red2", state="working")
    (sd / "sessiond.stopped.json").write_text('{"at": 1}')
    (r["state"] / "restart-pending.json").write_text("{}")
    (r["state"] / "server-id").write_text("same-id\n")
    (r["state"] / "trakt-token.json").write_text("{}")
    (r["state"] / "devices.json").write_text(json.dumps(
        [{"id": "d1", "name": "phone", "sha256": "h", "iss": "https://i", "rt": "spend-once"},
         {"id": "d2", "name": "tv", "sha256": "h2"}]))
    for name in ("matrix.env", "abs-bridge.env", "google-contacts-token.json",
                 "google-oauth-client.json", "secrets.env", "calendar-publish.json",
                 "config.toml.bak-before-sms"):
        (cfg / name).write_text("secret")
    (cfg / "config.toml").write_text("layout = 'default'\n")
    (r["home"] / ".config" / "agent-media.env").write_text("MEDIA_REMOTE_SAY_CMD=ssh p8a say\n")
    return r, sd, cfg


def test_no_chat_is_left_to_resume(copy, monkeypatch):
    r, sd, _cfg = copy
    got = sb.fence()
    for sid in ("working", "cutoff", "quiet"):
        rec = json.loads((sd / f"{sid}.json").read_text())
        assert rec["state"] == "ended" and not rec["cut_off"] and not rec["held"]
        assert not rec["queued"] and not rec["pid"]
    assert not (sd / "elsewhere.json").exists()
    assert not (sd / "sessiond.stopped.json").exists()
    assert not (r["state"] / "restart-pending.json").exists()
    assert got["chats_ended"] == 1 and got["resumes_dropped"] == 1
    assert got["remote_records_dropped"] == 1
    # What the real session host does with such records: nothing is due.
    from agent_media_server import sessiond

    sup = sessiond.Supervisor(root=sd)
    # And as a practice copy it ends no process on the machine it is beside.
    monkeypatch.setenv("MEDIA_REHEARSAL", "1")
    monkeypatch.setattr(sup, "_kill_orphans",
                        lambda ours: pytest.fail("a practice copy ended processes"))
    sup.load()
    assert set(sup.sessions) == {"working", "cutoff", "quiet"}
    assert not any(s.live or s.cut_off or s.held for s in sup.sessions.values())


def test_no_outside_credential_is_kept_and_the_env_file_is_not_loadable(copy):
    r, _sd, cfg = copy
    got = sb.fence()
    assert sorted(p.name for p in cfg.iterdir()) == ["config.toml"]
    assert not (r["state"] / "trakt-token.json").exists()
    assert len(got["credentials_deleted"]) == 8
    devs = json.loads((r["state"] / "devices.json").read_text())
    assert devs[0]["rt"] == "" and devs[0]["sha256"] == "h" and got["refresh_tokens_blanked"] == 1
    # The identity stays: it is the same server, and its devices still pair.
    assert (r["state"] / "server-id").read_text() == "same-id\n"
    assert not (r["home"] / ".config" / "agent-media.env").exists()
    assert (r["home"] / ".config" / "agent-media.env.restored-not-loaded").exists()


def test_the_units_run_on_the_copys_own_state_and_settings(copy):
    r, _sd, _cfg = copy
    env = sb.environment("https://red2-ryer.sasonica.com")
    assert env["XDG_STATE_HOME"] == str(r["home"] / ".local" / "state")
    assert env["MEDIA_SESSIOND_SOCKET"].startswith(str(sb.root()))
    assert env["MEDIA_ENV_FILE"] == str(sb.root() / "rehearsal.env")
    assert env["MEDIA_SERVERS_REPORT"] == "0" and env["MEDIA_SESSIOND_DEFAULT_HOST"] == "-"
    assert env["MEDIA_PUBLIC_URL"] == "https://red2-ryer.sasonica.com"
    assert len(env["MEDIA_SESSIOND_SOCKET"]) < 100          # a unix socket's limit is 108


class Fake:
    def __init__(self):
        self.ran = []
        self.proof_of = lambda n: server_address.proof(n)

    def __call__(self, cmd, host="", timeout=0):
        self.ran.append((host, cmd))
        if cmd == "echo $HOME":
            return 0, "/home/other"
        if cmd == sm.FIND_MEDIA:
            return 0, "/home/other/bin"
        if "sandbox start" in cmd:
            return 0, json.dumps({"ok": True, "fenced": {"chats_ended": 3, "resumes_dropped": 2,
                                                         "credentials_deleted": ["a"],
                                                         "refresh_tokens_blanked": 1},
                                  "practice": {"settle": {"adopted": 13, "not_resumed": 1,
                                                          "default_host_unset": True,
                                                          "folders_missing": [
                                                              {"cwd": "/x", "chats": 2}]},
                                               "units": {"written": 36, "would_enable": 22,
                                                         "release_can_run": 25, "other": []}}})
        if "server/probe?nonce=" in cmd:
            nonce = cmd.split("nonce=")[1].rstrip("'")
            return 0, json.dumps({"ok": True, "proof": self.proof_of(nonce)})
        return 0, "ok"


def test_settle_is_practised_on_the_copy_not_on_this_machines_state(copy, monkeypatch, tmp_path):
    r, sd, _cfg = copy
    from agent_media_server import server_settle, sessiond_remote

    monkeypatch.setattr(sessiond_remote.socket, "gethostname", lambda: "red2")
    real_state = server_settle._state()
    own = tmp_path / "own"
    own.mkdir()
    (own / "elsewhere.json").write_text(json.dumps({"session": "elsewhere", "state": "waiting",
                                                    "turns": 9}))
    (r["home"] / ".config" / "agent-media.env").write_text("MEDIA_SESSIOND_DEFAULT_HOST=red2\n")
    got = sb.practise_settle(sb.root(), own)
    assert got["adopted"] == 1 and got["default_host_unset"] and got["not_resumed"] == 2
    mine = json.loads((sd / "elsewhere.json").read_text())
    assert "host" not in mine and mine["turns"] == 9
    # Nothing was written to, and nothing still points at, the copy from here.
    assert server_settle._state() == real_state
    assert not (real_state / "moved-chats.json").exists()
    assert (r["state"] / "moved-chats.json").exists()


@pytest.fixture
def rig(monkeypatch):
    monkeypatch.setattr(backup, "backup", lambda **kw: {"ok": True, "id": "s1"})
    monkeypatch.setattr(backup, "sync", lambda **kw: {"ok": True, "id": "s2", "seconds": 4.0})
    monkeypatch.setitem(sm.RUN, "units", lambda st, run: st.update(units={"count": 2}))
    monkeypatch.setitem(sm.RUN, "transcripts", lambda st, run: st.setdefault(
        "transcripts", []).append({"ok": True, "files": 5, "seconds": 1.0}))
    monkeypatch.setattr(sm, "_rsync", lambda argv, stdin="": pytest.fail(f"a real rsync: {argv}"))
    monkeypatch.setattr(server_address, "reaches_here",
                        lambda url, timeout=15.0: (False, "no DNS yet"))
    return Fake()


def test_a_rehearsal_stops_nothing_here_and_overwrites_nothing_there(rig):
    said: list[str] = []
    rc = sm.rehearse("red2", "https://red2-ryer.sasonica.com", yes=True, run=rig, out=said.append)
    assert rc == 0, said
    cmds = [c for _h, c in rig.ran]
    assert not any("systemctl" in c for c in cmds)
    assert not any("media restore" in c or "install-services" in c or "rename" in c
                   for c in cmds)
    assert any("media server sandbox start --from /home/other/.local/share/agent-media-move/store"
               in c for c in cmds)
    text = "\n".join(said)
    assert "2 resumes dropped" in text and "final sync took 4.0 s" in text
    assert "13 of red2's own chats got their record back" in text and "missing: /x (2 chats)" in text
    assert "36 carried, 22 would be enabled" in text and "transcripts: 5 paths" in text
    assert any("--units /home/other/.local/share/agent-media-move/units" in c for c in cmds)
    assert "not reachable yet (no DNS yet)" in text


def test_without_yes_a_rehearsal_only_says_its_steps(rig):
    said: list[str] = []
    assert sm.rehearse("red2", "https://x.example", run=rig, out=said.append) == 0
    assert not rig.ran and "nothing done" in said[-1]


def test_a_copy_that_answers_as_another_server_fails_the_rehearsal(rig):
    rig.proof_of = lambda n: "someone-else"
    said: list[str] = []
    assert sm.rehearse("red2", "https://x.example", yes=True, run=rig, out=said.append) == 1
    assert "does not answer as this server" in said[-1]
