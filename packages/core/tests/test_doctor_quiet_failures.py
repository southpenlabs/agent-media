"""media doctor's quiet failures (28 Nov 2026, "Build all four")."""
import json

import pytest

from agent_media_core import cli, free, health_log


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    for d in ("HOME", "XDG_STATE_HOME", "XDG_CACHE_HOME", "XDG_CONFIG_HOME"):
        monkeypatch.setenv(d, str(tmp_path / d.lower()))


def _lines(**kw):
    return cli._doctor_quiet_failures(builds=kw.pop("builds", []), **kw)


def test_quiet_when_nothing_went_wrong():
    assert _lines() == []


def test_a_pickup_that_did_not_play_is_a_fault():
    health_log.note("restart_pickup", row=38349, played=False)
    health_log.note("restart_pickup", row=38350, played=True)
    got = _lines()
    assert len(got) == 1 and got[0][0] and "38349" in got[0][1]


def test_old_failures_age_out():
    health_log.note("restart_pickup", now=1.0, row=1, played=False)
    assert _lines() == []


def test_unconfirmed_replays_are_a_line_from_three():
    for _ in range(2):
        health_log.note("heard_unconfirmed")
    assert _lines() == []
    health_log.note("heard_unconfirmed")
    got = _lines()
    assert got and not got[0][0] and got[0][1].startswith("heard:")


def test_a_phone_whose_mic_watcher_is_off_is_a_fault():
    free.report("d_phone", {"call": False, "mic_watch": False})
    got = _lines()
    assert got and got[0][0] and "mic watcher" in got[0][1]
    free.report("d_phone", {"call": False})          # absent: watching again
    assert _lines() == []


def test_a_phone_behind_the_newest_build(monkeypatch):
    import time
    from agent_media_server import devices
    now = time.time()
    monkeypatch.setattr(devices, "list_devices", lambda: [
        {"id": "d1", "name": "Pixel 8a", "last_seen": now,
         "app": {"platform": "android", "build": "2851"}},
        {"id": "d2", "name": "TV", "last_seen": now - 10 * 86400,
         "app": {"platform": "android", "build": "2000"}}])
    old = [{"versionCode": 2855, "published": "2026-01-01T00:00:00Z", "apks": {"personal": {}}}]
    got = cli._doctor_quiet_failures(builds=old)
    assert len(got) == 1 and "Pixel 8a" in got[0][1] and "2855" in got[0][1] and not got[0][0]
    fresh = [{**old[0], "published": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now))}]
    assert cli._doctor_quiet_failures(builds=fresh) == []   # out less than a day: no nag


def test_health_log_keeps_the_last_few(monkeypatch):
    monkeypatch.setattr(health_log, "KEEP", 3)
    for i in range(5):
        health_log.note("x", row=i)
    rows = json.loads(health_log.path().read_text())["x"]
    assert [r["row"] for r in rows] == [2, 3, 4]
