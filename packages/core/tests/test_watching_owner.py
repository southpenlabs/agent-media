"""The Stop hook reads the thread-watching table only from the live server
that pinned it (watching.OWNER_NAME): a table written by another live
process (a test, a second server) reads as nothing open."""

from __future__ import annotations

import json
import os
import subprocess
import sys

from agent_media_core import watching


def _table(path, pid, sessions):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"pid": pid, "sessions": sessions}))


def test_the_owner_s_own_table_is_read(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    watching.pin()
    watching.publish_pinned({"s1": 1})
    try:
        assert watching.is_open("s1") and watching.open_sessions() == ["s1"]
    finally:
        watching._reset_for_tests()


def test_a_table_from_another_live_process_is_not_read(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    watching.pin()                                   # this process owns it
    other = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        _table(watching._path(), other.pid, {"s1": 1})
        assert not watching.is_open("s1") and watching.open_sessions() == []
    finally:
        other.kill()
        other.wait()
        watching._reset_for_tests()


def test_no_owner_file_or_a_dead_owner_reads_as_before(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    path = watching._path()
    _table(path, os.getpid(), {"s1": 1})
    assert watching.is_open("s1")                     # no owner file
    (path.parent / watching.OWNER_NAME).write_text("999999999")
    assert watching.is_open("s1")                     # owner long gone


def test_publish_pinned_writes_nothing_unpinned(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    watching._reset_for_tests()
    watching.publish_pinned({"s1": 1})
    assert not watching._path().exists()


def test_a_look_opens_the_thread_for_a_few_seconds(tmp_path, monkeypatch):
    # The light bubble polls the log with looking=1 and holds no stream
    # (David, 6 Nov 2026: "It may have been from a notification bubble").
    import time as _time

    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    assert not watching.is_open("b1")
    watching.look("b1")
    assert watching.is_open("b1") and "b1" in watching.open_sessions()
    later = _time.time() + watching.LOOK_S + 1
    monkeypatch.setattr(watching.time, "time", lambda: later)
    assert not watching.is_open("b1") and "b1" not in watching.open_sessions()
