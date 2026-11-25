"""A speech or music output picked to a device follows it when it pairs again,
and never stays on a device that is gone (25 Nov 2026: the Pixel 8a enrolled
again, speech stayed picked to the old id and every reply was refused).
Private state only; no real devices or playback."""

import pytest

from agent_media_server import devices, session_events, speech_frames as frames

CLAIMS = {"iss": "https://id.example", "sub": "u1", "email": "me@example.com"}


@pytest.fixture(autouse=True)
def private_state(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    monkeypatch.setattr(session_events, "poke", lambda: None)
    devices._reset_for_tests()
    frames._reset_for_tests()
    yield
    frames._reset_for_tests()
    devices._reset_for_tests()


def _pair(name="Pixel 8a", claims=CLAIMS):
    return devices.enrol_account(claims, name)["device_id"]


def _pick(device, channel="speech"):
    frames.listening(device, True, channel=channel)
    frames.select_output(channel, device)


def test_signed_out_then_enrolled_again_keeps_the_pick():
    old = _pair()
    _pick(old)
    _pick(old, "music")
    assert devices.revoke(old)
    assert frames.output_device("speech") is None  # Automatic meanwhile, not silence.
    new = _pair()
    assert frames.output_device("speech") == new
    assert frames.output_device("music") == new


def test_enrolled_again_before_the_old_one_signs_out_keeps_the_pick():
    # What the app did on 25 Nov 2026: the new pairing first, then the old
    # one signed out in the same second.
    old = _pair()
    _pick(old)
    new = _pair()
    assert devices.revoke(old)
    assert frames.output_device("speech") == new


def test_another_device_pairing_does_not_take_the_pick():
    old = _pair()
    _pick(old)
    devices.revoke(old)
    _pair("TCL Smart TV")
    _pair("Pixel 8a", {**CLAIMS, "sub": "u2", "email": "someone@example.com"})
    assert frames.output_device("speech") is None


def test_a_pick_made_by_hand_meanwhile_stands():
    old, tv = _pair(), _pair("TCL Smart TV")
    _pick(old)
    devices.revoke(old)
    _pick(tv)
    _pair()
    assert frames.output_device("speech") == tv


def test_the_pick_waits_a_day_at_most():
    old = _pair()
    _pick(old)
    frames.device_dropped(old, CLAIMS["email"], "Pixel 8a", now=1000.0)
    frames.device_added("d_new", CLAIMS["email"], "Pixel 8a", now=1000.0 + frames.SUCCESSOR_S + 1)
    assert frames.output_device("speech") is None


def test_revoking_an_account_frees_its_pick():
    old = _pair()
    _pick(old)
    devices.revoke_account(CLAIMS["email"])
    assert frames.output_device("speech") is None


def test_a_stale_pick_left_from_before_goes_to_automatic_and_speech_plays():
    phone = _pair()
    frames.listening(phone, True)
    frames._pick(frames._HUBS["speech"], "d_gone")  # As red2's music pick was.
    assert frames._HUBS["speech"].device() is None
    frames.forget_stale_picks({phone})
    assert frames.output_device("speech") is None
    assert frames._HUBS["speech"].device() == phone


def test_no_devices_known_clears_nothing():
    frames._pick(frames._HUBS["speech"], "d_phone")
    assert frames.forget_stale_picks(set()) == []
    assert frames.output_device("speech") == "d_phone"
