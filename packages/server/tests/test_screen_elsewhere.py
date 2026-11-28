"""Screens pause for another server's replies (agent-mail #1440, 28 Nov 2026:
hpo, paired with red2, played on through a reply in a thread red5 hosts).
A speaking peer (`pair --speaking`) posts its state to POST /screen/elsewhere
(screens.elsewhere); the sender is speaking_peers.Teller. No real network:
the sender's HTTP is a fake, and every path is under tmp_path."""

from __future__ import annotations

import os
import time

import pytest

from agent_media_server import auth, devices, mic_state, screens, speaking_peers, speech_frames
from test_contract import AUTH, call, server, typed  # noqa: F401 — fixtures
from test_mic import _device

HOME = "203.0.113.7"


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    for k in list(os.environ):
        if k.startswith(("MEDIA_", "RELAY_")):
            monkeypatch.delenv(k, raising=False)
    for d in ("HOME", "XDG_STATE_HOME", "XDG_CACHE_HOME", "XDG_CONFIG_HOME"):
        monkeypatch.setenv(d, str(tmp_path / d.lower()))
    monkeypatch.setenv("AMUX_AUTH_TOKEN", "hosttok")
    monkeypatch.setattr(screens, "_tailnet_peers", lambda: {})
    mic_state._reset()
    screens._reset()
    yield
    mic_state._reset()
    screens._reset()


def _peer(name="red5 media"):
    code, _ = devices.mint_code(name, speaking=True)
    got = devices.redeem(code, name)
    assert got["speaking"] is True
    return got["token"], got["device_id"]


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


@pytest.fixture()
def rig(monkeypatch):
    """A screen with the switch on at home and David's phone at home; this
    host itself is quiet."""
    tv_h, tv = _device("hpo screen")
    _, phone = _device("Pixel 8a")
    devices.set_pause(tv, True)
    seen = {phone: HOME}
    real = devices.list_devices

    def listed():
        rows = real()
        for r in rows:
            if r["id"] in seen:
                r["last_ip"], r["last_seen"] = seen[r["id"]], time.time()
        return rows
    monkeypatch.setattr(devices, "list_devices", listed)
    monkeypatch.setattr(speech_frames, "playing_device", lambda ch="speech": None)
    clock = Clock()
    monkeypatch.setattr(screens.time, "monotonic", clock)
    monkeypatch.setattr(auth, "client_ip", lambda: HOME)
    tv_bearer = tv_h["Authorization"].split()[1]

    def ask():
        ok, out = screens.now(tv_bearer, {"speaking": False})
        assert ok, out
        return out
    return {"ask": ask, "clock": clock, "seen": seen, "phone": phone, "tv": tv,
            "tv_bearer": tv_bearer}


def test_only_a_speaking_peer_may_post(rig):
    ok, out = screens.elsewhere(rig["tv_bearer"], {"speaking": True})
    assert not ok and out["status"] == 401
    ok, out = screens.elsewhere("nonsense", {"speaking": True})
    assert not ok and out["status"] == 401
    token, _ = _peer()
    assert screens.elsewhere(token, {"speaking": True}) == (True, {})


def test_a_speaking_peer_token_opens_nothing_else():
    token, _ = _peer()
    assert auth._device(token) is None
    assert devices.unused_for({"speaking": True}) is None


def test_peer_reply_pauses_once_long(rig):
    token, _ = _peer()
    screens.elsewhere(token, {"speaking": True, "dur": 12.0,
                              "player": {"name": "Pixel 8a", "install_hash": ""}})
    assert rig["ask"]() == {"on": True, "pause": True, "why": "reply"}


def test_a_beep_from_a_peer_waits_out_the_hold(rig):
    token, _ = _peer()
    body = {"speaking": True, "dur": 1.0, "player": {"name": "pixel-8a"}}
    screens.elsewhere(token, body)
    assert rig["ask"]()["pause"] is False
    rig["clock"].t += screens.SPEAK_HOLD_S
    screens.elsewhere(token, body)
    assert rig["ask"]()["pause"] is True


def test_a_stale_state_is_not_speaking_and_resumes(rig):
    token, _ = _peer()
    screens.elsewhere(token, {"speaking": True, "dur": 12.0, "player": {"name": "Pixel 8a"}})
    assert rig["ask"]()["pause"] is True
    rig["clock"].t += screens.ELSEWHERE_FRESH_S + 0.1
    rig["ask"]()
    rig["clock"].t += screens.RESUME_AFTER_S
    assert rig["ask"]()["pause"] is False


def test_thinking_from_a_peer_does_not_pause(rig):
    token, _ = _peer()
    screens.elsewhere(token, {"speaking": True, "thinking": True, "dur": 12.0})
    assert rig["ask"]()["pause"] is False


def test_player_away_from_home_does_not_pause(rig):
    token, _ = _peer()
    rig["seen"][rig["phone"]] = "198.51.100.9"
    screens.elsewhere(token, {"speaking": True, "dur": 12.0, "player": {"name": "Pixel 8a"}})
    assert rig["ask"]()["pause"] is False


def test_player_matched_by_install_hash_before_name(rig, monkeypatch):
    token, _ = _peer()
    devices.claim_install(rig["phone"], "install-0123456789abcdef")
    digest = devices.install_hash({"install": "install-0123456789abcdef"})
    rig["seen"][rig["phone"]] = "198.51.100.9"     # the phone is out
    # Named as something here that is home, but the hash says the phone.
    screens.elsewhere(token, {"speaking": True, "dur": 12.0,
                              "player": {"name": "hpo screen", "install_hash": digest}})
    assert rig["ask"]()["pause"] is False


def test_unplaced_player_counts_while_a_phone_is_home(rig):
    token, _ = _peer()
    screens.elsewhere(token, {"speaking": True, "dur": 12.0,
                              "player": {"name": "Some other tablet"}})
    assert rig["ask"]()["pause"] is True
    screens._reset()
    rig["seen"][rig["phone"]] = "198.51.100.9"
    screens.elsewhere(token, {"speaking": True, "dur": 12.0})
    assert rig["ask"]()["pause"] is False


def test_route_refuses_an_app_token_and_takes_a_peer(server):  # noqa: F811
    token, _ = _peer()
    res, obj = call(server, "POST", "/screen/elsewhere", {"speaking": True}, AUTH)
    assert res.status == 401, obj
    res, obj = call(server, "POST", "/screen/elsewhere", {"speaking": True},
                    {"Authorization": f"Bearer {token}"})
    assert res.status == 200 and obj["ok"], obj


# --- the sender ---------------------------------------------------------------

class Fake:
    def __init__(self):
        self.t = 0.0
        self.snap = {"speaking": False}
        self.busy = False
        self.sent = []
        self.snaps = 0

    def snapshot(self):
        self.snaps += 1
        return dict(self.snap)

    def teller(self, links=None):
        return speaking_peers.Teller(
            links=lambda: [{"alias": "red2", "url": "https://red2.example", "token": "t"}]
            if links is None else links,
            send=lambda link, body: self.sent.append((link["url"], body)),
            snapshot=self.snapshot, busy=lambda: self.busy,
            player=lambda: {"name": "Pixel 8a", "install_hash": "ab"},
            clock=lambda: self.t)


def test_sender_says_start_repeats_and_stop():
    f = Fake()
    t = f.teller()
    assert t.step()                                   # first look: quiet, said once
    assert f.sent[-1][1]["speaking"] is False
    f.t += 0.5
    assert not t.step()                               # idle hub: no snapshot spent
    assert f.snaps == 1
    f.busy, f.snap = True, {"speaking": True, "dur": 9.04}
    f.t += 0.5
    assert t.step()
    assert f.sent[-1] == ("https://red2.example", {
        "speaking": True, "thinking": False, "dur": 9.0,
        "player": {"name": "Pixel 8a", "install_hash": "ab"}})
    f.t += 0.5
    assert not t.step()                               # same state, not yet due
    f.t += 0.5
    assert t.step()                                   # repeated each second
    f.busy, f.snap = False, {"speaking": False}
    f.t += 0.5
    assert t.step()                                   # was speaking: looks, says stop
    assert f.sent[-1][1] == {"speaking": False, "thinking": False, "dur": None,
                             "player": None}


def test_sender_checks_now_and_then_when_the_hub_is_wrong():
    f = Fake()
    t = f.teller()
    t.step()
    f.snap = {"speaking": True, "dur": 5.0}           # hub says idle, wrongly
    f.t += speaking_peers.IDLE_CHECK_S
    assert t.step() and f.sent[-1][1]["speaking"] is True


def test_sender_without_links_does_nothing():
    f = Fake()
    t = f.teller(links=[])
    f.snap = {"speaking": True}
    assert not t.step() and f.snaps == 0 and not f.sent


def test_sender_survives_a_far_server_down():
    f = Fake()

    def boom(link, body):
        raise OSError("down")
    t = speaking_peers.Teller(links=lambda: [{"url": "https://x", "token": "t"}], send=boom,
                              snapshot=f.snapshot, busy=lambda: None,
                              player=lambda: None, clock=lambda: f.t)
    assert t.step()
