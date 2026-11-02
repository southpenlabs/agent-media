"""A song after the reply (reply_tune.py, intake/_text.extract_tune).

No player is reached: the hand-off player, the radio and Spotify are
stood in for, and the state lives in a scratch XDG_STATE_HOME."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from agent_media_core import radio, radio_io, reply_tune, spotify_remote as sr
from agent_media_core.intake._text import extract_tune, strip_markdown


@pytest.fixture(autouse=True)
def scratch(monkeypatch, tmp_path):
    for name in ("MEDIA_REPLY_TUNE", "MEDIA_REPLY_TUNE_SPOTIFY", "MEDIA_REPLY_TUNE_DEVICE",
                 "MEDIA_REPLY_TUNE_COOLDOWN_MIN", "MEDIA_PERSONAL_USE", "MEDIA_RADIO_HANDOFF_ENDPOINT"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setattr(radio, "is_on", lambda: False)


def _event(tune="Nina Simone - Feeling Good", **md):
    return SimpleNamespace(metadata={"tune": tune, **md})


class FakeHandoff:
    sent: list = []

    def __init__(self, where="handoff"):
        pass

    def props(self):
        return {"idle-active": True}

    def send(self, song, replace=False):
        FakeHandoff.sent.append((song, replace))
        return True

    def label(self, song):
        pass


@pytest.fixture()
def handoff(monkeypatch):
    FakeHandoff.sent = []
    monkeypatch.setenv("MEDIA_REPLY_TUNE", "handoff")
    monkeypatch.setattr(radio_io, "handoff_ready", lambda: None)
    monkeypatch.setattr(radio_io, "HandoffPlayer", FakeHandoff)
    return FakeHandoff


def test_the_marker_is_taken_out_of_the_words():
    text = "All green at last.\n\n[[tune: Queen - Don't Stop Me Now]]\n"
    assert extract_tune(text) == ("All green at last.", "Queen - Don't Stop Me Now")
    assert extract_tune("Done [[tune: X - Y]] here.") == ("Done here.", "X - Y")
    assert extract_tune("nothing here") == ("nothing here", None)
    assert "tune" not in strip_markdown("Done.\n[[tune: A - B]]\n[[sound: done]]")


def test_the_thread_does_not_show_it():
    from agent_media_server.transcript import display_text

    assert display_text("Done.\n\n[[tune: A - B]]") == "Done."


def test_off_by_default_plays_nothing(handoff, monkeypatch):
    monkeypatch.delenv("MEDIA_REPLY_TUNE")
    assert reply_tune.after_reply(_event(), 1) == "off (MEDIA_REPLY_TUNE)"
    assert handoff.sent == []


def test_handoff_asks_the_listeners_app_by_name(handoff):
    assert reply_tune.after_reply(_event(), 7) is None
    (song, replace), = handoff.sent
    assert song["q"] == "Nina Simone - Feeling Good" and replace is True
    assert len(song["id"]) == 11
    assert reply_tune.last()["row"] == 7


def test_one_per_cooldown(handoff):
    assert reply_tune.after_reply(_event(), 1) is None
    assert reply_tune.after_reply(_event("A - B"), 2) == "within the cooldown"
    assert len(handoff.sent) == 1


def test_not_over_music_or_a_station_or_a_thinking_readout(handoff, monkeypatch):
    assert reply_tune.after_reply(_event(thinking=True), 1) == "a thinking readout"
    monkeypatch.setattr(radio, "is_on", lambda: True)
    assert reply_tune.after_reply(_event(), 1) == "a radio station is on"
    monkeypatch.setattr(radio, "is_on", lambda: False)
    monkeypatch.setattr(FakeHandoff, "props", lambda self: {"idle-active": False, "pause": False})
    assert reply_tune.after_reply(_event(), 1) == "the hand-off player is playing"
    assert handoff.sent == []


def test_spotify_is_a_personal_use_method(monkeypatch):
    monkeypatch.setenv("MEDIA_REPLY_TUNE", "spotify")
    assert reply_tune.route() is None
    monkeypatch.setenv("MEDIA_PERSONAL_USE", "1")
    assert reply_tune.route() == "spotify"


def test_spotify_plays_the_top_hit_which_no_model_sees(monkeypatch):
    monkeypatch.setenv("MEDIA_REPLY_TUNE", "spotify")
    monkeypatch.setenv("MEDIA_PERSONAL_USE", "1")
    played = []
    monkeypatch.setattr(sr, "is_playing", lambda: False)
    monkeypatch.setattr(sr, "search_track", lambda a, t: {"uri": "spotify:track:1", "name": t, "artist": a})
    monkeypatch.setattr(sr, "play", lambda uri, device="": played.append((uri, device)))
    assert reply_tune.after_reply(_event(), 3) is None
    assert played == [("spotify:track:1", "")]
    assert reply_tune.last()["track"]["uri"] == "spotify:track:1"


def test_spotify_already_playing_is_left_alone(monkeypatch):
    monkeypatch.setenv("MEDIA_REPLY_TUNE", "spotify")
    monkeypatch.setenv("MEDIA_PERSONAL_USE", "1")
    monkeypatch.setattr(sr, "is_playing", lambda: True)
    assert reply_tune.after_reply(_event(), 1) == "Spotify is playing"


def test_split():
    assert reply_tune.split("Björk – Joga") == ("Björk", "Joga")
    assert reply_tune.split("Clair de Lune") == ("", "Clair de Lune")
