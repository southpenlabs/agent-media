"""Calm mode (calm.py): when it is on, and what it changes."""

from datetime import datetime

import pytest

from agent_media_core import calm, music_bed, music_bed_mix


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    for v in ("MEDIA_CALM", "MEDIA_CALM_FROM", "MEDIA_CALM_UNTIL", "MEDIA_CALM_MOOD",
              "MEDIA_CALM_DRIFT_S", "MEDIA_CALM_RATE", "MEDIA_CALM_VOLUME", "MEDIA_MUSIC_BED"):
        monkeypatch.delenv(v, raising=False)


def test_off_by_default():
    assert not calm.active()


def test_the_evening_window_runs_past_midnight():
    assert calm.in_evening(datetime(2026, 11, 1, 22, 0))
    assert calm.in_evening(datetime(2026, 11, 1, 3, 0))
    assert not calm.in_evening(datetime(2026, 11, 1, 6, 0))
    assert not calm.in_evening(datetime(2026, 11, 1, 14, 0))


def test_a_daytime_window(monkeypatch):
    monkeypatch.setenv("MEDIA_CALM_FROM", "13:00")
    monkeypatch.setenv("MEDIA_CALM_UNTIL", "15:30")
    assert calm.in_evening(datetime(2026, 11, 1, 14, 0))
    assert not calm.in_evening(datetime(2026, 11, 1, 16, 0))


def test_switches_through_the_music_settings():
    music_bed.set_settings({"calm_evening": True})
    assert not calm.active(datetime(2026, 11, 1, 12, 0))
    assert calm.active(datetime(2026, 11, 1, 23, 0))
    music_bed.set_settings({"calm": True})
    assert calm.active(datetime(2026, 11, 1, 12, 0))
    ids = {s["id"]: s for s in music_bed.settings()}
    assert ids["calm"]["value"] is True and ids["calm"]["set"] is True
    with pytest.raises(ValueError):
        music_bed.set_settings({"calm": "yes"})
    music_bed.set_settings({"calm": None, "calm_evening": None})
    assert not calm.active(datetime(2026, 11, 1, 23, 0))


def test_calm_beds_are_the_beatless_mood_whatever_the_choice():
    music_bed.set_choice("favourites:auto")
    assert music_bed.pick_for().source == "favourites"
    calm.set_stored("calm", True)
    p = music_bed.pick_for()
    assert (p.source, p.style) == ("generated", "night")
    music_bed.set_choice("off")
    assert music_bed.pick_for() is None   # off stays off


def test_the_mix_drifts_out_and_never_sings_out(tmp_path, monkeypatch):
    clip = tmp_path / "bed.wav"
    clip.write_bytes(b"x")
    monkeypatch.setenv("MEDIA_CALM_DRIFT_S", "90")
    normal = music_bed_mix.params("generated", "night", clip)
    calm.set_stored("calm", True)
    quiet = music_bed_mix.params("generated", "night", clip)
    assert quiet["outro_ms"] == 90000 != normal["outro_ms"]
    assert "after" not in quiet


def test_prosody():
    assert calm.prosody() == ("-10%", "-15%")
