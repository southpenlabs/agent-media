"""POST /speech/music-bed {"settings"}: a range's number reaches music_bed
(David, 31 Oct 2026: the music level slider "just switched back straight
away" — the server took only booleans and turned gain_db away with a 400)."""
from __future__ import annotations

import pytest

from agent_media_server import auth, pins


@pytest.fixture
def bed(monkeypatch, tmp_path):
    for k in list(__import__("os").environ):
        if k.startswith(("MEDIA_", "RELAY_")):
            monkeypatch.delenv(k, raising=False)
    for d in ("HOME", "XDG_STATE_HOME", "XDG_CACHE_HOME", "XDG_CONFIG_HOME"):
        monkeypatch.setenv(d, str(tmp_path / d.lower()))
    monkeypatch.setattr(auth, "gate", lambda b: ("david", None))
    from agent_media_core import music_bed
    monkeypatch.setattr(music_bed, "spawn_warm", lambda *a, **k: None)
    return music_bed


def _setting(detail, sid):
    return next(s for s in detail["settings"] if s["id"] == sid)


def test_a_number_sets_the_level(bed):
    ok, detail = pins.speech_music_bed("t", settings={"gain_db": -9})
    assert ok, detail
    assert bed.gain_db() == -9.0
    assert _setting(detail, "gain_db")["value"] == -9.0


def test_null_forgets_it_and_a_switch_still_works(bed):
    pins.speech_music_bed("t", settings={"gain_db": -9})
    ok, _ = pins.speech_music_bed("t", settings={"gain_db": None, "slow": False})
    assert ok
    assert bed.stored_gain() is None
    assert bed.stored_slow() is False


@pytest.mark.parametrize("bad", [{"gain_db": "loud"}, {"gain_db": 99}, {"gain_db": True},
                                 {"slow": 3}, {"nope": True}])
def test_a_bad_value_is_a_400(bed, bad):
    ok, detail = pins.speech_music_bed("t", settings=bad)
    assert not ok and detail["status"] == 400
    assert bed.stored_gain() is None
