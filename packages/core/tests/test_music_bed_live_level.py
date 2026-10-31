"""The Backing music level heard at once (David, 31 Oct 2026: "The backing
music slider doesn't seem to affect the backing music. should it straight
away while it's playing?"): a mix says the level it was made at, Settings
tells every phone the level now, and this host's rider follows it."""
from __future__ import annotations

import pytest

from agent_media_core import music_bed as B
from agent_media_core import music_bed_mix as M
from agent_media_core import under_mix
from agent_media_core.sinks import speech


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
    monkeypatch.setattr(B, "_settings_path", lambda: tmp_path / "music-bed.json")
    monkeypatch.setattr(B, "spawn_warm", lambda *a, **k: None)


def test_a_mix_says_the_level_it_was_made_at():
    B.set_gain(-12.0)
    assert M.params("generated", "calm", B.file_for("calm"))["level_db"] == -12.0


def test_settings_tells_the_phones(monkeypatch):
    told = []
    monkeypatch.setattr(B, "_push_level", lambda: told.append(B.gain_db()))

    class Now:  # run the push at once
        def __init__(self, target, **k):
            self.target = target

        def start(self):
            self.target()

    monkeypatch.setattr(B.threading, "Thread", Now)
    B.set_settings({"gain_db": -26})          # shown against the voice
    assert told == [-16.0]


def test_only_phone_targets_are_told_once_each(monkeypatch):
    import agent_media_core.audio_targets as at
    monkeypatch.setattr(at, "speech_options", lambda: [
        {"name": "phone"}, {"name": "phone2"}, {"name": "local"}])
    socks = {"phone": "tcp://p:1", "phone2": "tcp://p:1", "local": "/run/x.sock"}
    monkeypatch.setattr(speech, "_socket_for", lambda t: socks[t])
    sent = []
    monkeypatch.setattr(speech.ipc, "set_property", lambda s, n, v: sent.append((s, n, v)))
    assert speech.push_under_level(-16) == 1
    assert sent == [("tcp://p:1", speech.UNDER_LEVEL_PROPERTY, "-16.00")]


def test_the_rider_moves_by_the_difference():
    r = under_mix.Bed("/nowhere.sock", {"gain_db": -3.0, "level_db": -12.0})
    r.follow_level(-16.0)
    assert r.offset_db == -7.0
    r.follow_level(-12.0)
    assert r.offset_db == -3.0
    old = under_mix.Bed("/nowhere.sock", {"gain_db": -3.0})   # an older mix
    old.follow_level(-20.0)
    assert old.offset_db == -3.0
