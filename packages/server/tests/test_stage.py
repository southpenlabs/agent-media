"""Now playing on a big screen (stage.py, §6.21h; roadmap item 57; David,
6 Nov 2026: "assigning now playing to a large screen like a TV or
projector" — both sent from the phone and a screen set to always show it)."""

from __future__ import annotations

import os

import pytest

from agent_media_server import devices, follow, session_events, stage
from test_contract import AUTH, call, server, shelf, signed_in, typed  # noqa: F401
from test_mic import _device
from test_session_events import screen  # noqa: F401 — fixture
from test_thread_events import Stream

#: The real reader, before the fixture stands in for it.
_PLAYING = stage.playing


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    for k in list(os.environ):
        if k.startswith(("MEDIA_", "RELAY_")):
            monkeypatch.delenv(k, raising=False)
    for d in ("HOME", "XDG_STATE_HOME", "XDG_CACHE_HOME", "XDG_CONFIG_HOME"):
        monkeypatch.setenv(d, str(tmp_path / d.lower()))
    # Nothing plays unless a test says so: never the real speech or music.
    playing = {"reply": False, "music": False}
    monkeypatch.setattr(stage, "playing", lambda: dict(playing))
    follow._reset()
    yield playing
    follow._reset()


@pytest.fixture()
def rig():
    """An admin's phone (it may manage devices), a member's tablet (it may
    not) and a TV."""
    phone_h, phone = _device("Pixel 8a")
    devices.set_enrol(phone, True)
    tab_h, tab = _device("Tablet")
    devices.set_role(tab, "member")
    tv_h, tv = _device("Living-room TV")
    return {"phone_h": phone_h, "phone": phone, "tab_h": tab_h, "tab": tab,
            "tv_h": tv_h, "tv": tv}


def _row(device_id: str) -> dict:
    return next(d for d in devices.list_devices() if d["id"] == device_id)


def _someone_elses(device_id: str) -> None:
    """Make `device_id` another person's: an account's enrolment on its row."""
    with devices._locked():
        rows = devices._load(fresh=True)
        devices._save([{**d, "iss": "https://id.example", "sub": "someone-else"}
                       if d.get("id") == device_id else d for d in rows])


def test_any_device_of_the_same_person_sends_it_and_takes_it_off(server, rig):
    res, body = call(server, "POST", "/devices/stage", {"id": rig["tv"], "stage": "on"},
                     rig["tab_h"])
    assert res.status == 200, body
    assert body["stage"]["mode"] == "on" and body["stage"]["live"] is True
    assert body["stage"]["by"] == rig["tab"] and body["stage"]["by_name"] == "Tablet"
    assert body["stage"]["why"] == "sent" and body["stage"]["at"]
    assert _row(rig["tv"])["stage"] == "on"
    # Another of the same person's devices takes it off.
    res, body = call(server, "POST", "/devices/stage", {"id": rig["tv"], "stage": "off"},
                     rig["phone_h"])
    assert res.status == 200 and body["stage"]["mode"] == "off"
    assert body["stage"]["by"] is None and body["stage"]["at"] is None
    assert _row(rig["tv"])["stage"] == "off"


def test_who_may_send(server, rig):
    tv = rig["tv"]
    # Not a device: an ABS login has no screens.
    res, body = call(server, "POST", "/devices/stage", {"id": tv, "stage": "on"}, AUTH)
    assert res.status in (401, 403)
    res, _ = call(server, "POST", "/devices/stage", {"id": tv, "stage": "on"},
                  {"Authorization": "Bearer nobody"})
    assert res.status in (401, 403)
    # A guest reads and listens.
    devices.set_role(rig["tab"], "guest")
    res, body = call(server, "POST", "/devices/stage", {"id": tv, "stage": "on"}, rig["tab_h"])
    assert res.status == 403 and body["code"] == "guest"
    # Someone else's screen.
    _someone_elses(tv)
    res, body = call(server, "POST", "/devices/stage", {"id": tv, "stage": "on"},
                     rig["phone_h"])
    assert res.status == 403 and body["code"] == "not_yours"
    assert _row(tv)["stage"] == "off"


def test_bad_bodies(server, rig):
    tv, h = rig["tv"], rig["phone_h"]
    for bad in ({}, {"id": tv}, {"id": tv, "stage": "yes"}, {"id": tv, "stage": True},
                {"id": tv, "auto": "yes"}, {"stage": "on"}):
        res, body = call(server, "POST", "/devices/stage", bad, h)
        assert res.status == 400 and body["code"] == "bad_stage", (bad, body)
    res, body = call(server, "POST", "/devices/stage", {"id": "d_nope", "stage": "on"}, h)
    assert res.status == 404 and body["code"] == "no_such_device"


def test_always_is_set_as_the_mirroring_switch_is(server, rig, isolated):
    tv = rig["tv"]
    # A member's device may send, not set a screen to always show it.
    res, body = call(server, "POST", "/devices/stage", {"id": tv, "auto": True}, rig["tab_h"])
    assert res.status == 403 and body["code"] == "not_enrolled"
    res, body = call(server, "POST", "/devices/stage", {"id": tv, "stage": "auto"},
                     rig["phone_h"])
    assert res.status == 200, body
    st = body["stage"]
    assert (st["mode"], st["auto"], st["live"], st["why"]) == ("auto", True, False, None)
    # Something plays: it shows Now playing, and says why.
    isolated["reply"] = True
    assert (stage.state(tv)["live"], stage.state(tv)["why"]) == (True, "reply")
    isolated.update(reply=False, music=True)
    assert stage.state(tv)["why"] == "music"
    # /devices says so, with whether it shows now.
    res, body = call(server, "GET", "/devices", headers=rig["phone_h"])
    row = next(d for d in body["devices"] if d["id"] == tv)
    assert row["stage"] == "auto" and row["stage_auto"] is True and row["stage_live"] is True
    isolated["music"] = False
    res, body = call(server, "GET", "/devices", headers=rig["phone_h"])
    assert next(d for d in body["devices"] if d["id"] == tv)["stage_live"] is False
    # Off again from Settings.
    res, body = call(server, "POST", "/devices/stage", {"id": tv, "auto": False}, rig["phone_h"])
    assert res.status == 200 and body["stage"]["mode"] == "off"


def test_a_sent_stage_wins_and_ends_back_on_always(server, rig, isolated):
    tv = rig["tv"]
    devices.set_stage(tv, auto=True)
    call(server, "POST", "/devices/stage", {"id": tv, "stage": "on"}, rig["tab_h"])
    st = stage.state(tv)
    assert (st["mode"], st["auto"], st["live"], st["why"]) == ("on", True, True, "sent")
    # The screen takes it off itself: back to always, dark while nothing plays.
    res, body = call(server, "POST", "/device/stage", {"on": False}, rig["tv_h"])
    assert res.status == 200, body
    assert (body["mode"], body["auto"], body["live"]) == ("auto", True, False)


def test_the_screen_takes_it_off_but_never_puts_it_on(server, rig):
    tv = rig["tv"]
    devices.set_stage(tv, by=rig["phone"])
    res, body = call(server, "POST", "/device/stage", {"on": True}, rig["tv_h"])
    assert res.status == 400 and body["code"] == "bad_stage"
    assert _row(tv)["stage"] == "on"
    # A guest TV may take it off itself.
    devices.set_role(tv, "guest")
    res, body = call(server, "POST", "/device/stage", {"on": False}, rig["tv_h"])
    assert res.status == 200 and body["mode"] == "off"
    res, _ = call(server, "POST", "/device/stage", {"on": False}, AUTH)
    assert res.status == 401
    res, body = call(server, "GET", "/device/stage", headers=rig["tv_h"])
    assert res.status == 200 and body["mode"] == "off"


def test_the_screens_a_device_may_send_to(server, rig):
    other_h, other = _device("Someone's phone")
    _someone_elses(other)
    devices.set_stage(rig["tv"], by=rig["phone"])
    res, body = call(server, "GET", "/devices/stage", headers=rig["tab_h"])
    assert res.status == 200, body
    assert body["self"] == rig["tab"]
    got = {s["id"]: s for s in body["screens"]}
    # Not itself, not someone else's; a member may ask (it may send).
    assert set(got) == {rig["phone"], rig["tv"]}
    tv = got[rig["tv"]]
    assert (tv["name"], tv["stage"], tv["stage_live"], tv["by"], tv["by_name"]) == \
        ("Living-room TV", "on", True, rig["phone"], "Pixel 8a")
    devices.set_role(rig["tab"], "guest")
    res, _ = call(server, "GET", "/devices/stage", headers=rig["tab_h"])
    assert res.status == 403


def test_follow_answers_carry_the_stage_mirroring_or_not(server, rig):
    res, body = call(server, "GET", "/device/follow", headers=rig["tv_h"])
    assert body["phone"] is None and body["stage"]["mode"] == "off"
    devices.set_stage(rig["tv"], by=rig["phone"])
    res, body = call(server, "GET", "/device/follow", headers=rig["tv_h"])
    assert body["phone"] is None and body["stage"]["mode"] == "on"
    devices.set_follow(rig["tv"], rig["phone"])
    assert follow.state(rig["tv"])["stage"]["by"] == rig["phone"]


def test_the_stream_tells_the_screen_at_once(server, screen, rig, monkeypatch):
    """A screen with a stage and no mirroring hears it down `?follow=1`, as
    soon as it is sent and taken off."""
    st = Stream(server, "/sessions/events?ping=0.3&follow=1", rig["tv_h"])
    try:
        assert st.event()[0] == "sessions"
        ev, data = st.event()
        assert ev == "follow" and data["stage"]["mode"] == "off"
        monkeypatch.setattr(session_events, "POLL_S", 30.0)
        assert call(server, "POST", "/devices/stage", {"id": rig["tv"], "stage": "on"},
                    rig["phone_h"])[0].status == 200
        data = st.next("follow", 2.0)
        assert data["stage"]["mode"] == "on" and data["phone"] is None
        assert call(server, "POST", "/device/stage", {"on": False}, rig["tv_h"])[0].status == 200
        assert st.next("follow", 2.0)["stage"]["mode"] == "off"
    finally:
        st.close()


def test_always_hears_something_start_within_a_tick(server, screen, rig, monkeypatch, isolated):
    monkeypatch.setattr(session_events, "POLL_S", 0.2)
    devices.set_stage(rig["tv"], auto=True)
    st = Stream(server, "/sessions/events?ping=0.3&follow=1", rig["tv_h"])
    try:
        assert st.event()[0] == "sessions"
        data = st.next("follow", 2.0)
        assert data["stage"]["mode"] == "auto" and data["stage"]["live"] is False
        isolated["reply"] = True
        data = st.next("follow", 3.0)
        assert data["stage"]["live"] is True and data["stage"]["why"] == "reply"
        isolated["reply"] = False
        assert st.next("follow", 3.0)["stage"]["live"] is False
    finally:
        st.close()


def test_nothing_is_read_without_an_always_screen(rig, monkeypatch):
    asked = []
    monkeypatch.setattr(stage, "playing", lambda: asked.append(1) or {"reply": True, "music": False})
    assert stage.live_key() == ()
    devices.set_stage(rig["tv"], by=rig["phone"])
    assert stage.live_key() == () and stage.state(rig["tv"])["live"] is True
    assert asked == []
    devices.set_stage(rig["tv"], by=None, auto=True)
    assert stage.live_key() == (True, False)


def test_playing_reads_a_reply_not_a_thinking_line(monkeypatch):
    """The real `playing` (the fixture stands in for it elsewhere)."""
    from agent_media_server import music, speech

    monkeypatch.setattr(music, "_now", lambda: ({"pos_ms": 1000, "paused": False}, []))
    for state, want in (({"speaking": True}, True), ({"paused": True}, True),
                        ({"speaking": True, "thinking": True}, False),
                        ({"speaking": True, "notif": True}, False), ({}, False)):
        monkeypatch.setattr(speech, "current_state", lambda s=state: s)
        assert _PLAYING() == {"reply": want, "music": True}, state
    monkeypatch.setattr(music, "_now", lambda: ({"pos_ms": 1000, "paused": True}, []))
    assert _PLAYING()["music"] is False
