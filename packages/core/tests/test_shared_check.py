"""The Stop hook that keeps a reply's shared files (intake/shared_check.py)."""

import io
import json

import pytest

from agent_media_core import shared_files
from agent_media_core.intake import shared_check


@pytest.fixture
def root(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    r = tmp_path / "shared"
    monkeypatch.setenv("MEDIA_UPLOAD_DIR", str(r))
    shared_files.reset_cache()
    yield r
    shared_files.reset_cache()


def _run(monkeypatch, capsys, payload):
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
    assert shared_check.main() == 0
    out = capsys.readouterr().out.strip()
    return json.loads(out) if out else None


def _shot(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\x89PNG data")
    return path


def test_a_file_outside_the_root_is_copied_and_found_after_it_is_deleted(tmp_path, root, monkeypatch, capsys):
    shot = _shot(tmp_path / "wt" / "shot.png")
    msg = f"Done.\n\nShared file: {shot}"
    assert _run(monkeypatch, capsys, {"last_assistant_message": msg}) is None
    assert len(list(root.rglob("shot*.png"))) == 1
    shot.unlink()
    shared_files.reset_cache()
    assert shared_files.last_copy(str(shot)) is not None
    assert _run(monkeypatch, capsys, {"last_assistant_message": msg}) is None


def test_a_gone_file_blocks_the_stop_once(tmp_path, root, monkeypatch, capsys):
    gone = tmp_path / "wt" / "gone.png"
    msg = f"Look:\n\nShared file: {gone}"
    out = _run(monkeypatch, capsys, {"last_assistant_message": msg})
    assert out["decision"] == "block" and str(gone) in out["reason"]
    assert _run(monkeypatch, capsys, {"last_assistant_message": msg,
                                      "stop_hook_active": True}) is None


def test_a_file_already_in_the_root_and_a_reply_without_lines_pass(tmp_path, root, monkeypatch, capsys):
    kept = shared_files.share(_shot(tmp_path / "a.png"))
    assert _run(monkeypatch, capsys, {"last_assistant_message": f"Shared file: {kept}"}) is None
    assert _run(monkeypatch, capsys, {"last_assistant_message": "no files here"}) is None
    assert len(list(root.rglob("*.png"))) == 1


def test_a_hidden_file_is_not_copied(tmp_path, root, monkeypatch, capsys):
    secret = _shot(tmp_path / ".ssh" / "id.png")
    assert _run(monkeypatch, capsys, {"last_assistant_message": f"Shared file: {secret}"}) is None
    assert not root.exists() or not list(root.rglob("*.png"))


def test_off_switch(tmp_path, root, monkeypatch, capsys):
    monkeypatch.setenv("MEDIA_SHARED_CHECK", "0")
    msg = f"Shared file: {tmp_path / 'gone.png'}"
    assert _run(monkeypatch, capsys, {"last_assistant_message": msg}) is None


def test_setup_registers_it_as_a_guarded_sync_stop_hook(tmp_path):
    from agent_media_core import setup

    settings, changed = setup._merge_hook_entry(
        {}, "Stop", setup.shared_check_command(), setup.SHARED_CHECK_COMMAND, 10, False)
    entry = settings["hooks"]["Stop"][0]["hooks"][0]
    assert changed and "async" not in entry
    assert '*"Shared file: "*' in entry["command"] and setup.SHARED_CHECK_COMMAND in entry["command"]
