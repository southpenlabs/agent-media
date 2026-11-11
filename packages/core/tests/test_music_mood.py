"""The listening mood (agent_media_core.music_mood): the six, where it is
kept, and what it makes of the backing music's Auto. Its effect on a station
is pinned in test_radio.py, beside the station's faked phone."""

from __future__ import annotations

from datetime import datetime

import pytest

from agent_media_core import music_bed, music_mood


@pytest.fixture(autouse=True)
def _iso(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.delenv("MEDIA_MUSIC_MOOD", raising=False)
    monkeypatch.delenv("MEDIA_MUSIC_BED", raising=False)
    monkeypatch.setenv("MEDIA_MUSIC_BED_RENDER", "0")
    monkeypatch.setenv("MEDIA_MUSIC_BED_ASTRO", "off")


def test_the_six_in_order_and_auto_by_default():
    shown = music_mood.shown()
    assert shown["id"] == "auto"
    assert [m["id"] for m in shown["moods"]] == ["auto", "chill", "focus", "upbeat", "sleep", "party"]
    assert all(set(m) == {"id", "label", "blurb"} and m["label"] and m["blurb"] for m in shown["moods"])


def test_the_env_default_and_the_stored_one(monkeypatch):
    monkeypatch.setenv("MEDIA_MUSIC_MOOD", "focus")
    assert music_mood.current() == "focus"
    music_mood.set_mood("chill", playing=lambda: False)
    assert music_mood.current() == "chill"


def test_an_unknown_mood_is_refused():
    with pytest.raises(ValueError):
        music_mood.set_mood("sad")
    assert music_mood.current() == "auto"


def test_auto_tells_the_dj_nothing():
    assert music_mood.dj_means() is None
    assert music_mood.bed_auto(slow=True) is None


@pytest.mark.parametrize("mood,bed", [("chill", "dusk"), ("focus", "calm"), ("sleep", "night"),
                                      ("upbeat", "blend"), ("party", "morning")])
def test_the_beds_auto_follows_the_mood(monkeypatch, mood, bed):
    monkeypatch.setenv("MEDIA_MUSIC_BED_SLOW", "0")
    music_mood.set_mood(mood, playing=lambda: False)
    noon = datetime(2026, 11, 11, 12, 0)
    auto = music_bed.auto_now(noon, weather=None, axes=None)
    assert auto.mood == bed and auto.why == f"{bed} — mood: {mood}"
    assert music_bed.mood_for(noon, weather=None, pick="auto") == bed


def test_slow_only_still_holds_under_a_lively_mood(monkeypatch):
    monkeypatch.setenv("MEDIA_MUSIC_BED_SLOW", "1")
    music_mood.set_mood("party", playing=lambda: False)
    auto = music_bed.auto_now(datetime(2026, 11, 11, 12, 0), weather=None, axes=None)
    assert auto.mood in music_bed.SLOW_MOODS and auto.genre not in ("lofi", "jazz")
    assert "mood: party" in auto.why


def test_the_mood_beats_the_hour_and_the_rain(monkeypatch):
    monkeypatch.setenv("MEDIA_MUSIC_BED_SLOW", "0")
    music_mood.set_mood("chill", playing=lambda: False)
    auto = music_bed.auto_now(datetime(2026, 11, 11, 8, 0), weather="Rain", axes={"energy": 0.9})
    assert auto.mood == "dusk"


def test_a_fixed_bed_choice_wins_over_the_mood():
    music_mood.set_mood("sleep", playing=lambda: False)
    assert music_bed.mood_for(datetime(2026, 11, 11, 12, 0), weather=None, pick="morning") == "morning"
    assert music_bed.pick_for("piano", datetime(2026, 11, 11, 12, 0), weather=None).style == "piano"


def test_settings_say_why():
    music_mood.set_mood("chill", playing=lambda: False)
    assert "mood: chill" in music_bed.status()["auto_why"]
