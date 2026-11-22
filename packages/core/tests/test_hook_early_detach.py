"""The Stop path leaves Claude Code's process group before it builds the
reply (David, 30 Oct 2026: a phone chat parked 5 s after its Stop on a
thrashing host, and the half-started hook went with it: no speech, no play
key). Nothing here forks for real."""
import io
import json

from agent_media_core.intake import hook_claude_code as H


def _stop(monkeypatch):
    monkeypatch.delenv("MEDIA_HOOK_NO_EARLY_DETACH", raising=False)
    monkeypatch.delenv("MEDIA_HOOK_NO_DETACH", raising=False)
    monkeypatch.setattr(H, "load_env_file", lambda name: None)
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(
        {"hook_event_name": "Stop", "session_id": "s", "last_assistant_message": "Done."})))


def test_the_parent_returns_before_building_the_reply(monkeypatch):
    _stop(monkeypatch)
    handled = []
    monkeypatch.setattr(H.os, "fork", lambda: 4242)
    monkeypatch.setattr(H, "_handle_stop", lambda p: handled.append(p) or 0)
    assert H.main() == 0
    assert handled == []


def test_the_child_builds_it_and_plays_without_a_second_fork(monkeypatch):
    _stop(monkeypatch)
    forks, played = [], []
    monkeypatch.setattr(H.os, "fork", lambda: forks.append(1) or 0)
    monkeypatch.setattr(H.os, "setsid", lambda: None)
    monkeypatch.setattr(H, "_detach_stdio", lambda: None)
    monkeypatch.setattr(H, "_DETACHED", False)
    monkeypatch.setattr(H, "_play_now", lambda e: played.append(e.text))

    def stop(payload):
        H._play_detached(H.Event(text=payload["last_assistant_message"],
                                 source=H.Source.CLAUDE_CODE))
        return 0
    monkeypatch.setattr(H, "_handle_stop", stop)
    assert H.main() == 0
    assert forks == [1] and played == ["Done."]


def test_no_fork_runs_it_here(monkeypatch):
    _stop(monkeypatch)
    handled = []

    def nofork():
        raise OSError("no fork")
    monkeypatch.setattr(H.os, "fork", nofork)
    monkeypatch.setattr(H, "_handle_stop", lambda p: handled.append(p) or 0)
    assert H.main() == 0
    assert len(handled) == 1


def test_the_tests_switch_keeps_it_in_process(monkeypatch):
    _stop(monkeypatch)
    monkeypatch.setenv("MEDIA_HOOK_NO_EARLY_DETACH", "1")
    handled = []
    monkeypatch.setattr(H.os, "fork", lambda: (_ for _ in ()).throw(AssertionError("forked")))
    monkeypatch.setattr(H, "_handle_stop", lambda p: handled.append(p) or 0)
    assert H.main() == 0
    assert len(handled) == 1


def test_sessiond_second_try_is_held_not_spoken(monkeypatch):
    """MEDIA_HOOK_HOLD (sessiond `_recover_reply`): kept with its play key."""
    from agent_media_core.intake import toast
    monkeypatch.setenv("MEDIA_HOOK_HOLD", "1")
    held, played = [], []
    monkeypatch.setattr(toast, "hold", lambda e: held.append(e.text))
    monkeypatch.setattr(H, "_play_detached", lambda e: played.append(e.text))
    monkeypatch.setattr(H, "_source_place", lambda s="": {})
    assert H._handle_stop({"session_id": "s", "last_assistant_message": "Done."}) == 0
    assert held == ["Done."] and played == []


# ---- a scope of its own (15 Nov 2026) ------------------------------------------------
# A chat the session host runs is in the host's cgroup, and so is every process
# its hooks start: a restart of the host killed the reply's player before the
# reply was filed. Inside a systemd unit the Stop is handed to a copy of the
# hook in its own scope, with the same payload.

def _scoped(monkeypatch, wrap=True):
    from agent_media_core import procinfo
    _stop(monkeypatch)
    monkeypatch.setattr(procinfo, "outliving_service",
                        lambda cmd: (["systemd-run", "--user", "--scope", "--"] + cmd) if wrap else cmd)
    started = []

    class _Proc:
        def __init__(self, argv, **k):
            self.argv, self.env = argv, k["env"]
            self.stdin = io.BytesIO()
            self.stdin.close = lambda: started.append((self.argv, self.env, self.stdin.getvalue()))

    monkeypatch.setattr("subprocess.Popen", _Proc)
    monkeypatch.setattr(H.os, "fork", lambda: (_ for _ in ()).throw(AssertionError("forked")))
    return started


def test_inside_a_unit_the_stop_goes_to_a_scope_of_its_own(monkeypatch):
    started = _scoped(monkeypatch)
    monkeypatch.delenv("MEDIA_HOOK_SCOPED", raising=False)
    monkeypatch.setattr(H, "_handle_stop", lambda p: (_ for _ in ()).throw(AssertionError("handled here")))
    assert H.main() == 0
    (argv, env, data), = started
    assert argv[:3] == ["systemd-run", "--user", "--scope"]
    assert argv[-3:] == ["-P", "-m", "agent_media_core.intake.hook_claude_code"]
    assert env["MEDIA_HOOK_SCOPED"] == "1"
    assert json.loads(data)["last_assistant_message"] == "Done."


def test_the_scoped_copy_does_the_work(monkeypatch):
    started = _scoped(monkeypatch)
    monkeypatch.setenv("MEDIA_HOOK_SCOPED", "1")
    monkeypatch.setattr(H, "_detach_early", lambda: False)
    handled = []
    monkeypatch.setattr(H, "_handle_stop", lambda p: handled.append(p) or 0)
    assert H.main() == 0
    assert started == [] and handled and handled[0]["session_id"] == "s"


def test_outside_a_unit_nothing_changes(monkeypatch):
    started = _scoped(monkeypatch, wrap=False)
    monkeypatch.delenv("MEDIA_HOOK_SCOPED", raising=False)
    monkeypatch.setattr(H, "_detach_early", lambda: False)
    handled = []
    monkeypatch.setattr(H, "_handle_stop", lambda p: handled.append(p) or 0)
    assert H.main() == 0
    assert started == [] and len(handled) == 1


def test_the_scoped_copy_starts_from_a_folder_that_shadows_the_stdlib(tmp_path):
    """The Stop hook runs in the agent's last folder; core's package folder
    has a `types.py`. `-P` keeps it off the path, so the copy gets to main()."""
    import subprocess
    import sys

    (tmp_path / "types.py").write_text("raise SystemExit('shadowed')\n")
    env = {"PATH": "/usr/bin:/bin", "HOME": str(tmp_path), "MEDIA_HOOK_ENABLED": "0"}
    run = subprocess.run([sys.executable, "-P", "-m", "agent_media_core.intake.hook_claude_code"],
                         input="", cwd=tmp_path, env=env, capture_output=True, text=True, timeout=60)
    assert run.returncode == 0, run.stderr
