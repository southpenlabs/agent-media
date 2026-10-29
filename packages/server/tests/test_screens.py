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
    monkeypatch.setattr(speech_frames, "playing_device", lambda ch="speech": phone)
    clock = Clock()
    monkeypatch.setattr(screens.time, "monotonic", clock)
    ip = {"now": HOME}
    monkeypatch.setattr(auth, "client_ip", lambda: ip["now"])
    tv_bearer = tv_h["Authorization"].split()[1]

    def ask(state=None):
        ok, out = screens.now(tv_bearer, state or {})
        assert ok, out
        return out
    return {"ask": ask, "clock": clock, "seen": seen, "phone": phone, "guest": guest,
            "tv": tv, "ip": ip}


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
