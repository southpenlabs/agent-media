"""A conversation as the bridge's target, reached by its id (#491 step 5)."""

import subprocess

from tmux_voice_bridge import shim


def test_follow_the_last_thread_then_words_go_to_it(tmp_path, monkeypatch):
    monkeypatch.setattr(shim, "TARGET_FILE", tmp_path / "target")
    monkeypatch.setattr(shim, "offer_to_converse", lambda t: False)
    monkeypatch.setattr(shim, "last_thread", lambda: "sess-1")
    assert shim.handle_command("follow the last thread", {}) == "Following the last conversation."
    assert shim.load_thread() == "sess-1"
    runs = []
    monkeypatch.setattr(subprocess, "run", lambda argv, **k: runs.append(argv)
                        or subprocess.CompletedProcess(argv, 0, "", ""))
    monkeypatch.setattr(shim, "inject_local", lambda *a: runs.append("tmux!"))
    assert shim.do_inject("hello") is False
    assert runs == [["media", "session", "deliver", "sess-1", "--from", "voice", "--", "hello"]]


def test_no_tmux_and_no_target_goes_to_the_last_speaker(tmp_path, monkeypatch):
    monkeypatch.setattr(shim, "TARGET_FILE", tmp_path / "target")
    monkeypatch.setattr(shim, "offer_to_converse", lambda t: False)
    monkeypatch.setattr(shim.shutil, "which", lambda b: None)
    monkeypatch.setattr(shim, "last_thread", lambda: "sess-2")
    sent = []
    monkeypatch.setattr(shim, "inject_thread", lambda s, t: sent.append((s, t)) or True)
    shim.do_inject("hi")
    assert sent == [("sess-2", "hi")]
