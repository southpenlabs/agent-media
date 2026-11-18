"""`/targets` built once for requests that ask together (sessions._shared_build),
and the calendar's shifts answered while they are rebuilt (quiet._shift_index).
16 Nov 2026: 13 builds piled up on red2 while a new chat waited."""

from __future__ import annotations

import json
import os
import threading
import time

from agent_media_server import quiet, sessions


def test_requests_that_ask_together_share_one_build(monkeypatch):
    monkeypatch.setattr(sessions, "TARGETS_REUSE_S", 2.0)
    calls, gate = [], threading.Event()

    def build():
        calls.append(1)
        gate.wait(5)
        return {"sessions": [len(calls)]}

    got = []
    threads = [threading.Thread(target=lambda: got.append(sessions._shared_build("", build)))
               for _ in range(5)]
    for t in threads:
        t.start()
    time.sleep(0.2)
    gate.set()
    for t in threads:
        t.join(5)
    assert len(calls) == 1
    assert got == [{"sessions": [1]}] * 5
    # Just after: the same answer, no build.
    assert sessions._shared_build("", build) == {"sessions": [1]}
    assert len(calls) == 1


def test_a_write_between_means_a_fresh_build(monkeypatch):
    monkeypatch.setattr(sessions, "TARGETS_REUSE_S", 60.0)
    n = iter(range(10))
    build = lambda: {"n": next(n)}  # noqa: E731
    assert sessions._shared_build("", build) == {"n": 0}
    assert sessions._shared_build("", build) == {"n": 0}
    sessions.targets_changed()
    assert sessions._shared_build("", build) == {"n": 1}
    # The Everything list is its own.
    assert sessions._shared_build("all", build) == {"n": 2}


def test_a_finished_build_is_not_reused_past_its_window(monkeypatch):
    monkeypatch.setattr(sessions, "TARGETS_REUSE_S", 0.0)
    n = iter(range(10))
    build = lambda: {"n": next(n)}  # noqa: E731
    assert sessions._shared_build("", build) == {"n": 0}
    assert sessions._shared_build("", build) == {"n": 1}


def test_a_failed_build_is_not_shared(monkeypatch):
    monkeypatch.setattr(sessions, "TARGETS_REUSE_S", 60.0)

    def boom():
        raise RuntimeError("no")
    try:
        sessions._shared_build("", boom)
    except RuntimeError:
        pass
    assert sessions._shared_build("", lambda: {"ok": 1}) == {"ok": 1}


def test_shifts_past_their_minute_answer_at_once_and_rebuild(monkeypatch):
    quiet._reset_for_tests()
    reads, gate = [], threading.Event()

    def events():
        reads.append(1)
        if len(reads) > 1:
            gate.wait(5)
        return [{"managed": True, "shift_source": "work", "title": "Work — late",
                 "start_at": "2026-11-16T09:00:00", "end_at": "2026-11-16T17:00:00"}]
    monkeypatch.setattr(quiet, "_calendar_events", events)
    first = quiet.shifts("work")
    assert len(reads) == 1 and first
    at, spans, labels = quiet._SHIFTS
    quiet._SHIFTS = (at - quiet.SHIFT_TTL_S - 1, spans, labels)
    t0 = time.monotonic()
    assert quiet.shifts("work") == first  # the old index, not a wait
    assert time.monotonic() - t0 < 1
    gate.set()
    for _ in range(50):
        if not quiet._SHIFTS_REFRESHING.is_set():
            break
        time.sleep(0.05)
    assert len(reads) == 2
    assert quiet._SHIFTS[0] > at
    quiet._reset_for_tests()


def _main_list(monkeypatch, tmp_path, rows):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    path = sessions._saved_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"sessions": rows, "places": []}))
    return path


def test_a_fresh_server_answers_with_the_kept_list_and_builds_its_own(monkeypatch, tmp_path):
    _main_list(monkeypatch, tmp_path, [{"session": "old", "title": "Kept"}])
    monkeypatch.setattr(sessions, "TARGETS_SAVED_MAX_S", 900.0)
    monkeypatch.setattr(sessions.auth, "may_control_speech", lambda b: (True, {}))
    gate, built = threading.Event(), threading.Event()

    def build(key):
        gate.wait(5)
        built.set()
        return {"sessions": [{"session": "new", "title": "Fresh"}], "places": []}
    monkeypatch.setattr(sessions, "_build_targets", build)
    t0 = time.monotonic()
    ok, out = sessions.targets("tok")
    assert ok and time.monotonic() - t0 < 1     # not behind the build
    assert out["stale"] is True and out["sessions"][0]["title"] == "Kept"
    gate.set()
    assert built.wait(5)
    for _ in range(50):
        if sessions._BUILT_HERE.is_set():
            break
        time.sleep(0.02)
    # Once built here, its own list, and that is the one kept now.
    ok, out = sessions.targets("tok")
    assert "stale" not in out and out["sessions"][0]["title"] == "Fresh"
    assert json.loads(sessions._saved_path().read_text())["sessions"][0]["title"] == "Fresh"


def test_a_kept_list_too_old_is_not_answered_with(monkeypatch, tmp_path):
    path = _main_list(monkeypatch, tmp_path, [{"session": "old"}])
    monkeypatch.setattr(sessions, "TARGETS_SAVED_MAX_S", 900.0)
    old = time.time() - 1000
    os.utime(path, (old, old))
    assert sessions._saved() is None


def test_a_room_not_read_yet_is_not_read_in_the_list(monkeypatch, tmp_path):
    from agent_media_server import matrix

    monkeypatch.setattr(matrix, "ROWS_LOAD_INLINE", False)
    _main_list(monkeypatch, tmp_path, [
        {"session": "room-1", "harness": "matrix", "title": "Mel", "archived": True}])
    gate, loads = threading.Event(), []

    class SlowRoom:
        id, thread, loaded, lock = "!r:hs", "room-1", False, threading.RLock()

        def load(self):
            loads.append(1)
            gate.wait(5)

    room = SlowRoom()
    monkeypatch.setattr(matrix, "_ROOMS", {room.thread: room})
    from agent_media_server import archive_wake

    monkeypatch.setattr(archive_wake, "due", lambda f: [])
    t0 = time.monotonic()
    (row,) = matrix.rows()
    assert time.monotonic() - t0 < 1
    assert row["title"] == "Mel" and row["archived"] is False   # this list's flags
    gate.set()
    for _ in range(50):
        if loads:
            break
        time.sleep(0.02)
    assert loads == [1]
