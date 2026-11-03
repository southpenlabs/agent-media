"""A device that follows a phone (follow.py, §6.21g; roadmap item 52; David,
3 Nov 2026: "using the remote for sasonica is a bit cumbersome")."""

from __future__ import annotations

import os

import pytest

from agent_media_server import devices, follow, session_events
from test_contract import AUTH, SID, SID2, call, server, shelf, signed_in, typed  # noqa: F401
from test_mic import _device
from test_session_events import screen  # noqa: F401 — fixture
from test_thread_events import Stream


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    for k in list(os.environ):
        if k.startswith(("MEDIA_", "RELAY_")):
            monkeypatch.delenv(k, raising=False)
    for d in ("HOME", "XDG_STATE_HOME", "XDG_CACHE_HOME", "XDG_CONFIG_HOME"):
        monkeypatch.setenv(d, str(tmp_path / d.lower()))
    follow._reset()
    yield
    follow._reset()


@pytest.fixture()
def rig():
    """An admin's phone (it may manage devices) and a TV."""
    phone_h, phone = _device("Pixel 8a")
    devices.set_enrol(phone, True)
    tv_h, tv = _device("Living-room TV")
    return {"phone_h": phone_h, "phone": phone, "tv_h": tv_h, "tv": tv}


def test_the_switch_is_an_admins_and_kept_with_the_device(server, rig):
    res, _ = call(server, "POST", "/devices/follow", {"id": rig["tv"], "phone": rig["phone"]},
                  rig["tv_h"])
    assert res.status in (401, 403)
    res, body = call(server, "POST", "/devices/follow", {"id": rig["tv"], "phone": rig["phone"]},
                     rig["phone_h"])
    assert res.status == 200, body
    row = next(d for d in body["devices"] if d["id"] == rig["tv"])
    assert row["follow"] == rig["phone"]
    res, body = call(server, "POST", "/devices/follow", {"id": rig["tv"], "phone": None},
                     rig["phone_h"])
    assert res.status == 200
    assert next(d for d in body["devices"] if d["id"] == rig["tv"])["follow"] is None


def test_bad_bodies(server, rig):
    tv, h = rig["tv"], rig["phone_h"]
    for bad in ({"id": tv}, {"id": tv, "phone": 3}, {"id": tv, "phone": ""},
                {"id": tv, "phone": tv}):
        res, body = call(server, "POST", "/devices/follow", bad, h)
        assert res.status == 400, (bad, body)
    res, body = call(server, "POST", "/devices/follow", {"id": tv, "phone": "d_nope"}, h)
    assert res.status == 400 and body["code"] == "no_such_phone"
    res, body = call(server, "POST", "/devices/follow", {"id": "d_nope", "phone": None}, h)
    assert res.status == 404


def test_the_device_asks_what_it_follows(server, rig):
    off = {"ok": True, "phone": None, "name": None, "session": None, "title": None, "at": None}
    res, body = call(server, "GET", "/device/follow", headers=rig["tv_h"])
    assert res.status == 200 and body == off
    devices.set_follow(rig["tv"], rig["phone"])
    res, body = call(server, "GET", "/device/follow", headers=rig["tv_h"])
    assert body["phone"] == rig["phone"] and body["name"] == "Pixel 8a"
    assert body["session"] is None   # the phone has opened nothing since the start
    follow.opened(rig["phone"], SID)
    res, body = call(server, "GET", "/device/follow", headers=rig["tv_h"])
    assert body["session"] == SID and body["at"]
    # Not a device: nothing to follow with.
    res, _ = call(server, "GET", "/device/follow", headers=AUTH)
    assert res.status == 401


def test_a_followed_phone_revoked_is_following_nobody(server, rig):
    devices.set_follow(rig["tv"], rig["phone"])
    follow.opened(rig["phone"], SID)
    devices.revoke(rig["phone"])
    assert follow.state(rig["tv"])["phone"] is None


def test_a_key_press_turns_it_off_but_never_on(server, rig):
    devices.set_follow(rig["tv"], rig["phone"])
    res, body = call(server, "POST", "/device/follow", {"on": True}, rig["tv_h"])
    assert res.status == 400
    res, body = call(server, "POST", "/device/follow", {"on": False}, rig["tv_h"])
    assert res.status == 200 and body["phone"] is None
    row = next(d for d in devices.list_devices() if d["id"] == rig["tv"])
    assert row["follow"] is None
    res, _ = call(server, "POST", "/device/follow", {"on": False}, AUTH)
    assert res.status == 401


def test_the_same_thread_again_is_no_change():
    v = follow.version()
    follow.opened("d_phone", SID)
    assert follow.version() == v + 1
    follow.opened("d_phone", SID)
    assert follow.version() == v + 1
    follow.opened("d_phone", SID2)
    assert follow.version() == v + 2


def test_the_stream_names_the_phones_thread_at_once(server, screen, rig, monkeypatch):
    devices.set_follow(rig["tv"], rig["phone"])
    follow.opened(rig["phone"], SID)
    st = Stream(server, "/sessions/events?ping=0.3&follow=1", rig["tv_h"])
    try:
        assert st.event()[0] == "sessions"
        ev, data = st.event()
        assert ev == "follow" and data["phone"] == rig["phone"] and data["session"] == SID
        # The watcher's tick is far off now: only the open's own poke brings it.
        monkeypatch.setattr(session_events, "POLL_S", 30.0)
        follow.opened(rig["phone"], SID2)
        data = st.next("follow", 2.0)
        assert data["session"] == SID2 and data["title"] == "Sasonica web"
        # Another device opening a thread is nothing to the TV.
        follow.opened(rig["tv"], SID)
        assert st.event(1.0) == ("ping", {})
        # The TV's own key press: following ends, and the stream says so.
        assert call(server, "POST", "/device/follow", {"on": False}, rig["tv_h"])[0].status == 200
        assert st.next("follow", 2.0)["phone"] is None
    finally:
        st.close()


def test_no_follow_frame_unless_asked(server, screen, rig):
    devices.set_follow(rig["tv"], rig["phone"])
    st = Stream(server, "/sessions/events?ping=0.2", rig["tv_h"])
    try:
        assert st.event()[0] == "sessions"
        assert st.event(2.0) == ("ping", {})
    finally:
        st.close()


def test_opening_a_thread_stream_is_what_the_phone_has_open(server, screen, rig):
    st = Stream(server, f"/threads/{SID2}/events", rig["phone_h"])
    try:
        assert st.status == 200
        assert follow.state(rig["tv"])["phone"] is None   # the switch is off
        devices.set_follow(rig["tv"], rig["phone"])
        assert follow.state(rig["tv"])["session"] == SID2
    finally:
        st.close()
