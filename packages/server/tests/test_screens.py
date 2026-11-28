"""Devices that pause while someone talks or a reply is read out (screens.py,
§6.21f; David, 30 Oct 2026: "the option of assigning a media centre or big
screen")."""

from __future__ import annotations

import os
import time

import pytest

from agent_media_server import auth, devices, mic_state, screens, speech_frames
from test_contract import AUTH, call, server, typed  # noqa: F401 — fixtures
from test_mic import _device

HOME, AWAY = "203.0.113.7", "198.51.100.9"


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    for k in list(os.environ):
        if k.startswith(("MEDIA_", "RELAY_")):
            monkeypatch.delenv(k, raising=False)
    for d in ("HOME", "XDG_STATE_HOME", "XDG_CACHE_HOME", "XDG_CONFIG_HOME"):
        monkeypatch.setenv(d, str(tmp_path / d.lower()))
    monkeypatch.setenv("AMUX_AUTH_TOKEN", "hosttok")
    # Never ask the real tailnet.
    monkeypatch.setattr(screens, "_tailnet_peers", lambda: {"100.64.0.9": HOME})
    mic_state._reset()
    screens._reset()
    yield
    mic_state._reset()
    screens._reset()


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


@pytest.fixture()
def rig(monkeypatch):
    """A TV with the switch on at home, a phone, a guest's phone; the TV asks."""
    tv_h, tv = _device("Living-room TV")
    _, phone = _device("Pixel 8a")
    _, guest = _device("Sam's phone")
    devices.set_role(guest, "guest")
    devices.set_pause(tv, True)
    seen = {phone: HOME, guest: HOME}

    real = devices.list_devices

    def listed():
        rows = real()
        for r in rows:
            if r["id"] in seen:
                r["last_ip"], r["last_seen"] = seen[r["id"]], time.time()
        return rows
    monkeypatch.setattr(devices, "list_devices", listed)
    # Never this machine's tailnet: red2's has a "Living Room TV" node.
    monkeypatch.setattr(screens, "_tailnet_status", lambda: {})
    monkeypatch.setattr(speech_frames, "playing_device", lambda ch="speech": phone)
    clock = Clock()
    monkeypatch.setattr(screens.time, "monotonic", clock)
    ip = {"now": HOME}
    monkeypatch.setattr(auth, "client_ip", lambda: ip["now"])
    tv_bearer = tv_h["Authorization"].split()[1]

    reading = {"now": False}

    def ask(state=None):
        ok, out = screens.now(tv_bearer, state or {}, reading_here=lambda: reading["now"])
        assert ok, out
        return out
    return {"ask": ask, "clock": clock, "seen": seen, "phone": phone, "guest": guest,
            "tv": tv, "ip": ip, "reading": reading}


def _mic(dev, at):
    with mic_state._LOCK:
        mic_state._OPEN[dev] = (time.monotonic() + 30, at)


def test_off_says_so(rig):
    devices.set_pause(rig["tv"], False)
    assert rig["ask"]({"speaking": True}) == {"on": False, "pause": False, "why": None}


def test_mic_at_home_pauses_at_once(rig):
    _mic(rig["phone"], HOME)
    assert rig["ask"]() == {"on": True, "pause": True, "why": "mic"}


def test_mic_away_does_not(rig):
    _mic(rig["phone"], AWAY)
    assert rig["ask"]()["pause"] is False


def test_a_tailnet_phone_is_where_its_endpoint_is(rig):
    _mic(rig["phone"], "100.64.0.9")
    assert rig["ask"]()["pause"] is True


def test_a_guests_phone_does_not_pause(rig):
    _mic(rig["guest"], HOME)
    assert rig["ask"]()["pause"] is False


def test_pause_for_names_the_phones(rig):
    devices.set_pause(rig["tv"], True, [rig["guest"]])
    _mic(rig["phone"], HOME)
    assert rig["ask"]()["pause"] is False
    _mic(rig["guest"], HOME)
    assert rig["ask"]()["pause"] is True


def test_a_beep_alone_does_not_pause(rig):
    speaking = {"speaking": True}
    assert rig["ask"](speaking)["pause"] is False
    rig["clock"].t += 1.8                     # the longest earcon
    assert rig["ask"](speaking)["pause"] is False
    assert rig["ask"]({})["pause"] is False   # it ended: nothing to resume


def test_a_voice_that_goes_on_pauses_and_holds_through_gaps(rig):
    speaking = {"speaking": True}
    rig["ask"](speaking)
    rig["clock"].t += screens.SPEAK_HOLD_S
    assert rig["ask"](speaking) == {"on": True, "pause": True, "why": "reply"}
    rig["clock"].t += 0.5
    assert rig["ask"]({})["pause"] is True    # a gap between sentences
    rig["clock"].t += 0.5
    assert rig["ask"](speaking)["pause"] is True   # no new hold once paused
    rig["ask"]({})
    rig["clock"].t += screens.RESUME_AFTER_S
    assert rig["ask"]({})["pause"] is False


def test_a_long_gap_inside_a_reply_still_being_read_does_not_resume(rig):
    # David, 28 Nov 2026: "it keeps cutting in and out with the slightest
    # pause in the speech" — the voice stops at each gap, the reply does not.
    rig["reading"]["now"] = True
    assert rig["ask"]({"speaking": True, "dur": 6.0})["pause"] is True
    rig["ask"]({})
    rig["clock"].t += screens.RESUME_AFTER_S + 2.0   # the next sentence is late
    assert rig["ask"]({})["pause"] is True
    rig["reading"]["now"] = False                    # the reply is over
    rig["ask"]({})
    rig["clock"].t += 0.1
    assert rig["ask"]({})["pause"] is False


def test_a_wedged_reply_cannot_hold_a_screen_paused(rig):
    rig["reading"]["now"] = True
    rig["ask"]({"speaking": True, "dur": 6.0})
    rig["ask"]({})
    rig["clock"].t += screens.READING_HOLD_MAX_S
    assert rig["ask"]({})["pause"] is False


def test_reading_alone_never_starts_a_pause(rig):
    rig["reading"]["now"] = True
    assert rig["ask"]({})["pause"] is False


def test_a_voice_longer_than_any_beep_pauses_at_once(rig):
    assert rig["ask"]({"speaking": True, "dur": 12.4}) == {"on": True, "pause": True, "why": "reply"}


def test_a_short_clip_still_waits_for_the_hold(rig):
    assert rig["ask"]({"speaking": True, "dur": 1.5})["pause"] is False
    assert rig["ask"]({"speaking": True, "dur": None})["pause"] is False


def test_a_long_thinking_read_out_still_does_not_pause(rig):
    assert rig["ask"]({"speaking": True, "thinking": True, "dur": 9})["pause"] is False


def test_thinking_does_not_pause(rig):
    st = {"speaking": True, "thinking": True}
    rig["ask"](st)
    rig["clock"].t += 10
    assert rig["ask"](st)["pause"] is False


def test_a_voice_on_a_phone_away_does_not_pause(rig):
    rig["seen"][rig["phone"]] = AWAY
    rig["ask"]({"speaking": True})
    rig["clock"].t += 5
    assert rig["ask"]({"speaking": True})["pause"] is False


def test_the_screen_somewhere_unknown_never_pauses(rig):
    rig["ip"]["now"] = "127.0.0.1"
    _mic(rig["phone"], HOME)
    assert rig["ask"]()["pause"] is False


def test_place():
    assert screens.place("203.0.113.7") == "203.0.113.7"
    assert screens.place("100.64.0.9") == HOME
    assert screens.place("100.64.0.10") == ""
    assert screens.place("127.0.0.1") == "" and screens.place("") == ""


# --- the routes -----------------------------------------------------------------

def test_routes(server):
    admin_h, admin = _device("Pixel 8a")
    devices.set_enrol(admin, True)
    tv_h, tv = _device("Living-room TV")
    res, body = call(server, "GET", "/screen/now", headers=tv_h)
    assert res.status == 200 and body == {"ok": True, "on": False, "pause": False, "why": None}
    res, body = call(server, "POST", "/devices/pause", {"id": tv, "on": True}, admin_h)
    assert res.status == 200, body
    row = next(d for d in body["devices"] if d["id"] == tv)
    assert row["pause_here"] is True and row["pause_for"] == []
    res, body = call(server, "GET", "/screen/now", headers=tv_h)
    assert body["on"] is True and body["pause"] is False   # from loopback: nowhere known


def test_pause_switch_is_an_admins(server):
    tv_h, tv = _device("Living-room TV")
    res, _ = call(server, "POST", "/devices/pause", {"id": tv, "on": True}, tv_h)
    assert res.status in (401, 403)
    admin_h, admin = _device("Pixel 8a")
    devices.set_enrol(admin, True)
    for bad in ({"id": tv}, {"id": tv, "on": "yes"}, {"id": tv, "on": True, "for": "x"}):
        res, _ = call(server, "POST", "/devices/pause", bad, admin_h)
        assert res.status == 400
    res, _ = call(server, "POST", "/devices/pause", {"id": "d_nope", "on": True}, admin_h)
    assert res.status == 404


def test_check_ins_feed_the_doctor(rig, monkeypatch):
    """28 Nov 2026: hpo's old ducker was dead two weeks and nothing said so."""
    screens._SEEN.clear()
    screens._SEEN_WRITTEN.clear()
    tv = rig["tv"]
    now = 1_000_000.0
    assert any("has not asked" in l for _, l in screens.doctor_lines(now, ("v2", now)))
    screens._check_in(tv, "v1", now)
    assert screens.doctor_lines(now + 5, ("v1", now)) == []
    lines = screens.doctor_lines(now + screens.QUIET_S + 1, ("v1", now))
    assert lines and not lines[0][0] and "last asked" in lines[0][1]   # a line, not a fault
    # Still asking on the old version long after the new one came out: a fault.
    bad = screens.doctor_lines(now + 60, ("v2", now - screens.BEHIND_S))
    assert bad == [(True, bad[0][1])] and "self-update" in bad[0][1]
    assert screens.doctor_lines(now + 60, ("v2", now)) == []   # just released: give it time


def test_a_computer_left_off_is_never_a_fault(rig):
    """hpo is off a week at a time: a release while it is off is no fault,
    only a quiet line."""
    screens._SEEN.clear()
    screens._SEEN_WRITTEN.clear()
    t0 = 1_000_000.0
    screens._check_in(rig["tv"], "v1", t0)            # last seen, then switched off
    week = t0 + 7 * 86400
    lines = screens.doctor_lines(week, ("v2", t0 + 3600))   # v2 out while it was off
    assert lines and all(not bad for bad, _ in lines)


def test_check_ins_are_written_at_most_once_a_minute(rig):
    import json
    screens._SEEN.clear()
    screens._SEEN_WRITTEN.clear()
    screens._check_in("d1", "v1", 1000.0)
    screens._check_in("d1", "v1", 1030.0)
    assert json.loads(screens.seen_path().read_text())["d1"]["at"] == 1000.0
    screens._check_in("d1", "v2", 1031.0)     # a new version is written at once
    assert json.loads(screens.seen_path().read_text())["d1"]["helper"] == "v2"
    screens._check_in("d1", "", 1100.0)       # an app that names no version keeps the last
    assert json.loads(screens.seen_path().read_text())["d1"] == {"at": 1100.0, "helper": "v2"}


def test_a_computer_that_is_on_with_a_silent_helper_is_a_fault(rig):
    """agent-mail #1449: hpo came back from a crash, its helper failed on
    every start, and the doctor said "asleep". On the tailnet, or another of
    its devices seen, after the helper went quiet: broken, not asleep."""
    screens._SEEN.clear()
    screens._SEEN_WRITTEN.clear()
    t0 = 1_000_000.0
    screens._check_in(rig["tv"], "v1", t0)
    later = t0 + 3600
    dev = {"id": rig["tv"], "name": "hpo screen", "last_ip": "100.70.1.2"}
    online = lambda: {"Peer": {"k": {"HostName": "HPO", "TailscaleIPs": ["100.70.1.9"],  # noqa: E731
                                     "Online": True}}}
    assert "online on the tailnet" in screens.computer_on(dev, t0, later, tailnet=online, rows=[])
    seen = lambda: {"Peer": {"k": {"HostName": "x", "TailscaleIPs": ["100.70.1.2"],  # noqa: E731
                                   "Online": False, "LastSeen": "1970-01-12T14:00:00Z"}}}
    assert "was on the tailnet" in screens.computer_on(dev, t0, later, tailnet=seen, rows=[])
    # Off: the node was last seen before the helper went quiet, nothing else of it seen.
    off = lambda: {"Peer": {"k": {"HostName": "hpo", "TailscaleIPs": ["100.70.1.2"],  # noqa: E731
                                  "Online": False, "LastSeen": "1970-01-12T13:46:40Z"}}}
    assert screens.computer_on(dev, t0, later, tailnet=off, rows=[]) == ""
    # Another of its devices seen since: on.
    rows = [{"id": "d_jobs", "name": "hpo jobs", "last_seen": later - 60}]
    assert "'hpo jobs' was seen" in screens.computer_on(dev, t0, later, tailnet=dict, rows=rows)
    # A phone on the same home address is no evidence.
    home = {"id": rig["tv"], "name": "hpo screen", "last_ip": "203.0.113.5"}
    rows = [{"id": "d_ph", "name": "Pixel", "last_ip": "203.0.113.5", "last_seen": later}]
    assert screens.computer_on(home, t0, later, tailnet=dict, rows=rows) == ""
    # And the doctor makes it a fault.
    real = screens.computer_on
    try:
        screens.computer_on = lambda d, a, n, tailnet=None, rows=None: "hpo is online on the tailnet"
        bad = screens.doctor_lines(later, ("v1", t0))
        assert bad and bad[0][0] and "helper is not running" in bad[0][1]
    finally:
        screens.computer_on = real
