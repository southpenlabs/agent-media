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
