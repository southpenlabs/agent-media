"""memwatch: the host is strained when tasks stall on memory, and the alert is
raised only after a run of strained ticks, cleared below half the line."""

import pytest

from agent_media_server import memwatch, procmem


@pytest.fixture
def reports(monkeypatch):
    got = []
    monkeypatch.setattr(memwatch, "report", lambda level, title="", detail="":
                        got.append((level, title, detail)))
    monkeypatch.setattr(memwatch, "_host", lambda: "box")
    monkeypatch.setattr(procmem, "top_processes", lambda n=5: [
        {"pid": 7, "name": "java", "rss_mb": 958, "swap_mb": 81}])
    for k in ("MEDIA_MEMWATCH", "MEDIA_MEM_STRAIN_PCT", "MEDIA_MEM_STRAIN_TICKS"):
        monkeypatch.delenv(k, raising=False)
    return got


def host(stall):
    return {"mem_total_mb": 7758, "mem_available_mb": 900, "swap_total_mb": 9914,
            "swap_free_mb": 277, "mem_stall_pct": stall}


def test_strained_is_the_stall_against_the_line(monkeypatch):
    monkeypatch.delenv("MEDIA_MEM_STRAIN_PCT", raising=False)
    assert memwatch.strained(host(None)) is None
    assert memwatch.strained(host(9.9)) is False
    assert memwatch.strained(host(10.0)) is True
    monkeypatch.setenv("MEDIA_MEM_STRAIN_PCT", "5")
    assert memwatch.strained(host(6)) is True


def test_a_run_of_strained_ticks_raises_once_and_a_low_stall_clears(reports):
    w = memwatch.Watch()
    assert w.tick(host(2)) == "ok"            # the first tick clears what a restart left
    assert [w.tick(host(30)) for _ in range(3)] == [None, None, None]
    assert w.tick(host(30)) == "warn"
    level, title, detail = reports[-1]
    assert level == "warn" and "box" in title and "30 %" in title
    assert "java (7) 958 MB + 81 MB swapped" in detail and "Swap: 9.4 GB used" in detail
    assert w.tick(host(40)) is None           # raised once
    assert w.tick(host(7)) is None            # under the line, not under half: held
    assert w.tick(host(4.9)) == "ok"
    assert [r[0] for r in reports] == ["ok", "warn", "ok"]


def test_one_calm_tick_restarts_the_run(reports):
    w = memwatch.Watch()
    for stall in (30, 30, 30, 8, 30, 30, 30):
        assert w.tick(host(stall)) in (None, "ok")
    assert "warn" not in [r[0] for r in reports]


def test_no_psi_and_off_say_nothing(reports, monkeypatch):
    w = memwatch.Watch()
    assert w.tick(host(None)) is None
    monkeypatch.setenv("MEDIA_MEMWATCH", "0")
    assert w.tick(host(50)) is None
    assert reports == []
