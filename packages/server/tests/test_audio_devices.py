"""Named frame players: isolated state, no real devices or playback."""

import pytest

from agent_media_core import audio_targets
from agent_media_server import audio_devices, devices, session_events, speech_frames as frames


@pytest.fixture(autouse=True)
def private_players(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    monkeypatch.setattr(session_events, "poke", lambda: None)
    monkeypatch.setattr(devices, "list_devices", lambda: [
        {"id": "d_phone", "name": "David’s phone"}, {"id": "d_tv", "name": "Living-room TV"}])
    monkeypatch.setattr(audio_targets, "speech_configured", lambda t: (True, None))
    monkeypatch.setattr(audio_targets, "music_available", lambda t: (True, None))
    frames._reset_for_tests()
    yield
    frames._reset_for_tests()


def test_picked_phone_is_not_stolen_by_a_tv_connecting_or_reconnecting():
    phone = frames.listening("d_phone", True)
    frames.select_output("speech", "d_phone")
    tv = frames.listening("d_tv", True)
    assert frames._HUBS["speech"].device() == "d_phone"
    assert frames.playing_stream(phone) and not frames.playing_stream(tv)
    newer = frames.listening("d_phone", True)
    assert frames.playing_stream(newer) and not frames.playing_stream(phone)
    frames.listening("d_phone", False, newer)
    frames.listening("d_phone", False, phone)
    assert frames._HUBS["speech"].device() is None
    assert not frames.playing_stream(tv)  # An offline choice never becomes the TV.


def test_music_and_speech_choices_are_separate_and_persist():
    for ch in ("speech", "music"):
        frames.listening("d_phone", True, channel=ch)
        frames.listening("d_tv", True, channel=ch)
    audio_devices.select("music", "device:d_tv")
    audio_devices.select("speech", "device:d_phone")
    assert frames.output_device("music") == "d_tv"
    assert frames.output_device("speech") == "d_phone"
    frames._reset_for_tests()
    frames.listening("d_phone", True)
    frames.listening("d_tv", True)
    assert frames._HUBS["speech"].device() == "d_phone"


def test_current_audio_moves_with_a_targeted_stop_and_playhead_snapshot():
    phone = frames.listening("d_phone", True)
    frames.select_output("speech", "d_phone")
    tv = frames.listening("d_tv", True)
    hub = frames._HUBS["speech"]
    frames._send([{"op": "load", "uri": "https://example.test/one", "mode": "replace"},
                  {"op": "load", "uri": "https://example.test/two", "mode": "append"},
                  {"op": "pos", "i": 1}, {"op": "seek", "t": 8},
                  {"op": "pause", "on": True}], hub)
    old = frames.frames_after(0)[-1]
    frames.select_output("speech", "d_tv")
    stop, snapshot = frames.frames_after(old["seq"])
    assert stop["ops"] == [{"op": "stop"}]
    assert frames.playing_stream(phone, frame=stop) and not frames.playing_stream(tv, frame=stop)
    assert frames.playing_stream(tv, frame=snapshot) and not frames.playing_stream(phone, frame=snapshot)
    assert not frames.playing_stream(tv, frame=old)  # No stale phone clips on the TV.
    assert {"op": "pos", "i": 1} in snapshot["ops"]
    assert next(o["t"] for o in snapshot["ops"] if o["op"] == "seek") == pytest.approx(8, abs=0.1)
    assert {"op": "pause", "on": True} in snapshot["ops"]
    assert hub.model.pos == 1 and hub.model.entries == ["https://example.test/one", "https://example.test/two"]


def test_picker_shows_named_devices_and_offline_choice():
    phone = frames.listening("d_phone", True)
    frames.select_output("speech", "d_phone")
    frames.listening("d_phone", False, phone)
    frames.listening("d_tv", True)
    block = audio_devices.block("speech", {"current": "sasonica", "options": []})
    assert block["current"] == "device:d_phone" and block["overridden"]
    rows = {r["name"]: r for r in block["options"]}
    assert rows["device:d_phone"]["label"] == "David’s phone"
    assert not rows["device:d_phone"]["available"]
    assert rows["device:d_tv"]["available"]
    assert rows["device:d_tv"]["label"] == "Living-room TV"


def test_automatic_stays_the_ticked_row_and_names_where_it_plays():
    # David, 4 Nov 2026: "I don't seem to be able to select Automatic" — the
    # block answered the last-used device as current, so Automatic never ticked.
    frames.listening("d_phone", True)
    frames.used("d_phone")
    frames.listening("d_tv", True)
    base = {"current": "sasonica", "overridden": False,
            "options": [{"name": "sasonica", "label": "Phone (Sasonica)", "available": True}]}
    block = audio_devices.block("speech", base)
    assert block["current"] == "sasonica" and not block["overridden"]
    assert block["plays_on"] == "device:d_phone"
    auto = next(r for r in block["options"] if r["name"] == "sasonica")
    assert auto["label"] == "Automatic app player" and "David’s phone" in auto["why"]
    frames.select_output("speech", "d_tv")
    block = audio_devices.block("speech", base)
    assert block["current"] == "device:d_tv" and block["overridden"]


def test_only_a_paired_connected_device_can_be_selected():
    with pytest.raises(ValueError, match="unknown paired"):
        audio_devices.select("speech", "device:d_unknown")
    with pytest.raises(ValueError, match="not connected"):
        audio_devices.select("speech", "device:d_tv")
    assert frames.output_device("speech") is None


def test_automatic_follows_the_device_used_last_not_the_one_that_reconnected():
    # David, 4 Nov 2026: a restart reconnected the TV after the phone, and the
    # TV took the speech though nobody had used it in a day.
    phone = frames.listening("d_phone", True)
    frames.used("d_phone")
    tv = frames.listening("d_tv", True)
    assert frames._HUBS["speech"].device() == "d_phone"
    assert frames.playing_stream(phone) and not frames.playing_stream(tv)
    # Across a restart: both reconnect, the TV last.
    frames._reset_for_tests()
    frames.listening("d_phone", True)
    tv = frames.listening("d_tv", True)
    assert frames._HUBS["speech"].device() == "d_phone"
    # Using the TV brings it there.
    frames.used("d_tv")
    assert frames.playing_stream(tv)


def test_nothing_used_yet_is_the_newest_stream_as_before():
    frames.listening("d_phone", True)
    tv = frames.listening("d_tv", True)
    assert frames.playing_stream(tv)


def test_using_a_device_moves_what_is_playing_at_its_playhead():
    frames.listening("d_phone", True)
    frames.used("d_phone")
    frames.listening("d_tv", True)
    hub = frames._HUBS["speech"]
    frames._send([{"op": "load", "uri": "https://example.test/one", "mode": "replace"},
                  {"op": "seek", "t": 4}], hub)
    frames.used("d_tv")
    stop, snap = frames.frames_after(0)[-2:]
    assert stop["device"] == "d_phone" and stop["ops"] == [{"op": "stop"}]
    assert snap["device"] == "d_tv" and snap["ops"][0]["uri"] == "https://example.test/one"


def test_a_picked_speaker_holds_against_use_elsewhere():
    tv = frames.listening("d_tv", True)
    frames.select_output("speech", "d_tv")
    phone = frames.listening("d_phone", True)
    frames.used("d_phone")
    assert frames.playing_stream(tv) and not frames.playing_stream(phone)


def test_a_device_not_connected_takes_nothing():
    tv = frames.listening("d_tv", True)
    frames.used("d_phone")
    assert frames.playing_stream(tv)
    phone = frames.listening("d_phone", True)  # It reconnects: used last, so it plays.
    assert frames.playing_stream(phone)
