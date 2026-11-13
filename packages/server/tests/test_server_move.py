"""`media server move` (server_move.py): the order of the steps, what stops
it, and what a rollback puts back. Nothing is run: every command goes to a
fake `run`, the backup and the address check are stand-ins, and the log is
under the test's own state dir.
"""

from __future__ import annotations

import pytest

from agent_media_server import backup, server_address, server_move as sm

NAME = "https://red2-ryer.sasonica.com"
OLD = "https://red5.sasonica.com"


class Fake:
    def __init__(self):
        self.ran: list[tuple[str, str]] = []
        self.fail: dict[str, tuple[int, str]] = {}

    def __call__(self, cmd, host="", timeout=0):
        self.ran.append((host, cmd))
        for key, got in self.fail.items():
            if key in cmd:
                return got
        if cmd == "echo $HOME":
            return 0, "/home/other"
        if cmd == sm.FIND_MEDIA:
            return 0, "/home/other/rel/bin"
        if "list-units" in cmd and "stop" not in cmd:
            return 0, "agent-media-sessiond.service\nagent-media-jobs-worker.service"
        return 0, "ok"


@pytest.fixture
def rig(monkeypatch):
    fake = Fake()
    made: list[tuple[str, str]] = []
    monkeypatch.setenv("MEDIA_PUBLIC_URL", OLD)
    monkeypatch.setattr(backup, "backup", lambda **kw: made.append(("backup", kw["to"])) or
                        {"ok": True, "id": "s1"})
    monkeypatch.setattr(backup, "sync", lambda **kw: made.append(("sync", kw["to"])) or
                        {"ok": True, "id": "s2", "seconds": 3.0})
    monkeypatch.setattr(sm, "up_units", lambda: ["agent-media-state-gc.timer",
                                                 "agent-media-visual-canvas.service",
                                                 "agent-media-sessiond.service"])
    monkeypatch.setattr(sm, "enabled_units", lambda: ["agent-media-state-gc.timer",
                                                      "agent-media-visual-canvas.service"])
    monkeypatch.setattr(sm, "busy_chats", lambda to="": [])
    # RUN holds the step functions themselves, so the table is what is
    # patched: a step left real here once read this machine's unit files and
    # tried to rsync them to red2 (13 Nov 2026).
    monkeypatch.setitem(sm.RUN, "units", lambda st, run: made.append(("units", st["to"])))
    monkeypatch.setitem(sm.RUN, "transcripts", lambda st, run: (
        made.append(("transcripts", st["to"])), st.setdefault("transcripts", []).append({"ok": True})))
    monkeypatch.setattr(sm, "_rsync", lambda argv, stdin="": pytest.fail(f"a real rsync: {argv}"))
    monkeypatch.setattr(sm.subprocess, "run",
                        lambda *a, **k: pytest.fail(f"a real command from a test: {a}"))
    monkeypatch.setattr(sm, "carry_transcripts",
                        lambda st: made.append(("transcripts", st["to"])) or {"ok": True, "files": 3})
    monkeypatch.setattr(server_address, "reaches_here", lambda url, timeout=15.0: (True, ""))
    return fake, made


def _move(fake, **kw):
    said: list[str] = []
    kw.setdefault("foreground", True)
    rc = sm.move("red2", NAME, [], run=fake, out=said.append, **kw)
    return rc, "\n".join(said)


def test_without_yes_it_says_the_steps_and_does_nothing(rig):
    fake, made = rig
    rc, said = _move(fake)
    assert rc == 0 and "nothing done" in said and "7 verify" in said
    assert not fake.ran and not made and not sm.log_path().exists()


def test_the_steps_run_in_order(rig):
    fake, made = rig
    rc, said = _move(fake, yes=True)
    assert rc == 0, said
    st = sm._load()
    assert st["done"] == list(sm.STEPS) and st["finished"]
    assert st["there_store"] == "/home/other/.local/share/agent-media-move"
    assert made == [("backup", "red2:/home/other/.local/share/agent-media-move/store"),
                    ("transcripts", "red2"), ("units", "red2"),
                    ("sync", "red2:/home/other/.local/share/agent-media-move/store"),
                    ("transcripts", "red2")]
    cmds = [c for _h, c in fake.ran]
    # Its own state is kept before anything of this server's lands there.
    keep = next(i for i, c in enumerate(cmds) if "media backup run" in c and "/before" in c)
    stop = next(i for i, c in enumerate(cmds) if c.startswith("systemctl --user stop agent-media"))
    restore = next(i for i, c in enumerate(cmds) if "media restore latest" in c and "--only" not in c)
    install = next(i for i, c in enumerate(cmds) if "install-services" in c)
    rename = next(i for i, c in enumerate(cmds) if "media server rename" in c)
    stash = next(i for i, c in enumerate(cmds) if "settle --stash" in c)
    hooks = next(i for i, c in enumerate(cmds) if "install-hooks" in c)
    settle = next(i for i, c in enumerate(cmds) if "media server settle --own" in c
                  and "--stash" not in c)
    dropins = next(i for i, c in enumerate(cmds) if "--only .config/systemd/user" in c)
    others = next(i for i, c in enumerate(cmds) if "settle --units" in c)
    assert "release-mode" in cmds[others]
    assert keep < stop < stash < restore < dropins < install < others < hooks < settle < rename
    assert "disable --now agent-media-jobs-worker" in cmds[-1]
    # Here the session host stops last; there the old name keeps answering.
    here_stops = [c for h, c in fake.ran if not h and "systemctl --user stop" in c]
    assert here_stops[-1].endswith("agent-media-sessiond.service")
    # And nothing here is left enabled to start a second copy at a reboot.
    assert ("", "systemctl --user disable agent-media-state-gc.timer "
                "agent-media-visual-canvas.service") in fake.ran
    assert f"--to {NAME}" in cmds[rename] and f"--also {OLD}" in cmds[rename]
    assert "--no-restart" in cmds[rename]
    # The other machine's own agent-media is first on the PATH for each.
    assert cmds[rename].startswith("PATH=/home/other/rel/bin:$PATH; media server rename")
    # Everything about the restore went to the other machine.
    assert all(h == "red2" for h, c in fake.ran if "media restore" in c or "rename" in c)


def test_a_machine_without_a_release_stops_it_before_anything_is_touched(rig):
    fake, made = rig
    fake.fail["grep -q 'restore'"] = (0, "")
    rc, said = _move(fake, yes=True)
    assert rc == 1 and "media update --no-restart" in said and "nothing here was stopped" in said
    assert not made and not any("systemctl --user stop" in c for _h, c in fake.ran)


def test_a_failed_backup_stops_nothing(rig, monkeypatch):
    fake, _made = rig
    monkeypatch.setattr(backup, "backup", lambda **kw: {"ok": False, "warnings": ["disk full"]})
    rc, said = _move(fake, yes=True)
    assert rc == 1 and "disk full" in said
    assert not any("systemctl --user stop" in c for _h, c in fake.ran)


def test_the_stop_and_after_run_detached_unless_foreground(rig):
    fake, made = rig
    units: list[tuple[str, str]] = []
    rc, said = _move(fake, yes=True, foreground=False,
                     detach=lambda to, name: units.append((to, name)) or "media-server-move-x")
    assert rc == 0 and units == [("red2", NAME)] and "--status" in said
    assert sm._load()["done"] == ["check", "keep", "backup", "transcripts", "units"]
    assert not any("systemctl --user stop" in c for _h, c in fake.ran)
    # The detached half carries on from the log.
    said2: list[str] = []
    assert sm.move("red2", NAME, [], fire=True, run=fake, out=said2.append) == 0
    assert sm._load()["done"] == list(sm.STEPS)


def test_a_name_that_does_not_answer_fails_verify_and_undoes_nothing(rig, monkeypatch):
    fake, _made = rig
    monkeypatch.setattr(server_address, "reaches_here",
                        lambda url, timeout=15.0: (False, f"{url} did not answer"))
    monkeypatch.setattr(sm.time, "sleep", lambda s: None)
    st_wait = {"n": 0}
    real = sm.time.time

    def clock():
        st_wait["n"] += 1
        return real() + st_wait["n"] * 60
    monkeypatch.setattr(sm.time, "time", clock)
    rc, said = _move(fake, yes=True)
    assert rc == 1 and "did not answer" in said and "--rollback" in said
    assert not any("systemctl --user start agent-media-sessiond.service" == c for _h, c in fake.ran)
    assert sm._load()["failed"]["step"] == "verify"


def test_rollback_puts_the_other_machine_back_and_starts_this_one(rig):
    fake, _made = rig
    _move(fake, yes=True)
    fake.ran.clear()
    said: list[str] = []
    assert sm.rollback(run=fake, out=said.append) == 0
    there = [c for h, c in fake.ran if h == "red2"]
    assert any("media restore latest" in c and "/before" in c for c in there)
    assert any("systemctl --user start agent-media-sessiond.service agent-media-jobs-worker.service"
               in c for c in there)
    here = [c for h, c in fake.ran if not h]
    assert here == ["systemctl --user enable agent-media-state-gc.timer "
                    "agent-media-visual-canvas.service",
                    "systemctl --user start agent-media-state-gc.timer",
                    "systemctl --user start agent-media-visual-canvas.service",
                    "systemctl --user start agent-media-sessiond.service"]
    assert sm._load()["rolled_back"] and "by hand" in said[-1]


def test_rollback_before_the_stop_touches_nothing_there(rig):
    fake, made = rig
    fake.fail["media backup run"] = (1, "no room")
    _move(fake, yes=True)
    fake.ran.clear()
    assert sm.rollback(run=fake, out=lambda s: None) == 0
    assert not fake.ran


def test_status(rig):
    fake, _made = rig
    said: list[str] = []
    assert sm.status(out=said.append) == 0 and "no server move" in said[0]
    _move(fake, yes=True)
    said.clear()
    assert sm.status(out=said.append) == 0 and "finished" in said[-1]


def test_a_working_chat_refuses_the_stop_unless_forced(rig, monkeypatch):
    fake, _made = rig
    monkeypatch.setattr(sm, "busy_chats",
                        lambda to="": ["session 12345678 (fix the thing) is working"])
    rc, said = _move(fake, yes=True)
    assert rc == 1 and "is working" in said and "--force" in said
    assert not any("systemctl --user stop" in c for _h, c in fake.ran)
    assert sm._load()["failed"]["step"] == "stop"
    fake.ran.clear()
    said2: list[str] = []
    assert sm.move("red2", NAME, [], yes=True, foreground=True, force=True, run=fake,
                   out=said2.append) == 0


def test_transcripts_are_listed_for_this_servers_chats_only(tmp_path, monkeypatch):
    from agent_media_server import sessiond

    root = tmp_path / ".claude" / "projects"
    (root / "-home-x-a").mkdir(parents=True)
    (root / "-home-x-a" / "s1.jsonl").write_text("{}")
    (root / "-home-x-a" / "s1" / "subagents").mkdir(parents=True)
    (root / "-home-x-a" / "s2.jsonl").write_text("{}")       # a chat on another host
    (root / "-home-x-a" / "zz.jsonl").write_text("{}")       # not one of the server's
    monkeypatch.setattr(sessiond, "records", lambda root=None: [
        {"session": "s1"}, {"session": "s2", "host": "red2"}, {"session": "s3"}])
    assert sm.transcript_list(tmp_path) == ["-home-x-a/s1.jsonl", "-home-x-a/s1/"]


def test_transcripts_are_not_carried_between_different_homes(monkeypatch):
    monkeypatch.undo()
    got = sm.carry_transcripts({"to": "red2", "there_home": "/somewhere/else"})
    assert not got["ok"] and "homes differ" in got["skipped"]


def test_the_servers_own_unit_files_are_collected(tmp_path, monkeypatch):
    """install-services has no template for the canvas: its unit file (a
    symlink into a checkout on red5) is carried, with what it requires, read
    through the link; the house audio and a jobs worker are not."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    src = sm.units_dir()
    src.mkdir(parents=True)
    real = tmp_path / "checkout" / "agent-media-visual-canvas.service"
    real.parent.mkdir()
    real.write_text("[Unit]\nRequires=tailnet-ip.service\nAfter=network-online.target "
                    "tailnet-ip.service\n[Service]\nExecStart=x\n")
    (src / "agent-media-visual-canvas.service").symlink_to(real)
    (src / "tailnet-ip.service").write_text("[Service]\nExecStart=y\n")
    (src / "agent-media-state-gc.service").write_text("[Service]\nExecStart=z\n")
    (src / "agent-media-state-gc.timer").write_text("[Timer]\nOnCalendar=daily\n")
    (src / "agent-media-jobs-worker.service").write_text("x")
    (src / "am-sinks.service").write_text("x")
    (src / "unrelated.service").write_text("x")
    (src / "agent-media-visual-canvas.service.d").mkdir()
    dest = tmp_path / "out"
    got = sm.collect_units(dest, enabled=lambda n: n.endswith((".timer", "canvas.service")))
    assert got["units"] == ["agent-media-state-gc.service", "agent-media-state-gc.timer",
                            "agent-media-visual-canvas.service", "tailnet-ip.service"]
    assert not (dest / "agent-media-visual-canvas.service").is_symlink()
    assert "Requires=tailnet-ip.service" in (dest / "agent-media-visual-canvas.service").read_text()
    import json as _json
    assert _json.loads((dest / "enabled.json").read_text()) == [
        "agent-media-state-gc.timer", "agent-media-visual-canvas.service"]


def test_the_stop_order_and_what_is_never_stopped(monkeypatch):
    monkeypatch.undo()
    active = ["agent-media-sessiond.service", "agent-media-backup.timer",
              "agent-media-tmux-hold-amux-scratch.service", "am-sinks.service",
              "agent-media-visual-canvas.service", "agent-media-x.socket"]
    monkeypatch.setattr(sm, "_unit_names", lambda *a: active)
    assert sm.up_units() == ["agent-media-backup.timer", "agent-media-visual-canvas.service",
                             "am-sinks.service", "agent-media-sessiond.service"]
    assert "agent-media-tmux-hold-amux-scratch.service" not in sm.enabled_units()


def test_fire_carries_on_from_the_failed_step_and_keeps_what_the_stop_recorded(rig,
                                                                               monkeypatch):
    """The cutover (13 Nov 2026) failed at `restore` with the stop done. A
    second `--fire` must not run `stop` again: it would record the units it
    now sees (none are up) and `--rollback` would have nothing to start."""
    fake, made = rig
    monkeypatch.setitem(sm.RUN, "restore", lambda st, run: (_ for _ in ()).throw(
        sm.Stop("red2: keeping its own chats' records failed")))
    rc, said = _move(fake, yes=True)
    assert rc == 1 and "restore" in said
    st = sm._load()
    assert st["done"] == ["check", "keep", "backup", "transcripts", "units", "stop", "sync"]
    stopped, disabled = st["stopped_here"], st["disabled_here"]
    assert stopped and disabled
    # Nothing is up now, as after the real stop.
    monkeypatch.setattr(sm, "up_units", lambda: [])
    monkeypatch.setattr(sm, "enabled_units", lambda: [])
    monkeypatch.setitem(sm.RUN, "restore", lambda st, run: made.append(("restore", st["to"])))
    said2: list[str] = []
    assert sm.move("red2", NAME, [], fire=True, run=fake, out=said2.append) == 0
    st = sm._load()
    assert st["done"] == list(sm.STEPS) and st["finished"] and not st.get("failed")
    assert st["stopped_here"] == stopped and st["disabled_here"] == disabled
    assert "stop" not in "\n".join(said2)
    said3: list[str] = []
    assert sm.move("red2", NAME, [], fire=True, run=fake, out=said3.append) == 0
    assert "every step is done" in "\n".join(said3)
