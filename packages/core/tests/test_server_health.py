"""server_health: the doctor's line for the server's memory and threads."""

import os

from agent_media_core import server_health as sh


def test_the_line_says_memory_peak_threads_and_arenas():
    f = {"unit": "agent-media-visual-canvas.service", "pid": 1, "rss_mb": 309,
         "threads": 42, "peak_mb": 1576, "arenas": 1, "arena_mb": 8}
    assert sh.line(f) == ("server: 309 MB resident, peak 1576 MB, 42 threads, "
                          "1 arenas holding 8 MB (agent-media-visual-canvas)")


def test_past_the_mark_it_is_called_high():
    f = {"unit": "x.service", "pid": 1, "rss_mb": 1500, "threads": 3,
         "peak_mb": None, "arenas": None, "arena_mb": None}
    assert sh.line(f) == "server: 1500 MB resident, 3 threads (x) — high"


def test_a_unit_not_running_has_nothing_to_say(monkeypatch):
    monkeypatch.setattr(sh, "_show", lambda name, *p: {"MainPID": "0", "ActiveState": "inactive"})
    assert sh.facts() is None


def test_facts_read_this_process(monkeypatch):
    monkeypatch.setattr(sh, "_show", lambda name, *p: {
        "MainPID": str(os.getpid()), "ActiveState": "active", "MemoryPeak": str(200 << 20)})
    f = sh.facts("t.service")
    assert f["pid"] == os.getpid() and f["rss_mb"] > 0 and f["threads"] >= 1
    assert f["peak_mb"] == 200 and f["unit"] == "t.service"


def test_the_unit_can_be_named(monkeypatch):
    monkeypatch.setenv("MEDIA_SERVER_UNIT", "agent-media-server.service")
    assert sh.unit() == "agent-media-server.service"
