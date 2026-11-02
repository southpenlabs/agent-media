"""Methods only a personal install may run (personal_use.py), and what a
public install does instead."""

from __future__ import annotations

import json

import pytest

from agent_media_core import music_links, personal_use, practice_library


@pytest.fixture()
def public(monkeypatch):
    for name, (env, _why) in personal_use.METHODS.items():
        monkeypatch.delenv(env, raising=False)
    monkeypatch.delenv("MEDIA_PERSONAL_USE", raising=False)


def test_nothing_on_a_public_install_and_all_on_a_personal_one(public, monkeypatch):
    assert personal_use.report() == {"on": False, "methods": {m: False for m in personal_use.METHODS}}
    monkeypatch.setenv("MEDIA_PERSONAL_USE", "1")
    assert all(personal_use.report()["methods"].values())
    assert not personal_use.allowed("something-else")


def test_a_methods_own_setting_wins_either_way(public, monkeypatch):
    monkeypatch.setenv("MEDIA_STORIES", "off")
    monkeypatch.setenv("MEDIA_PERSONAL_USE", "1")
    assert not personal_use.allowed("facebook-stories")
    monkeypatch.delenv("MEDIA_PERSONAL_USE")
    monkeypatch.setenv("MEDIA_PRACTICE_EMBED_PLAY", "page")
    assert personal_use.allowed("insight-timer-page") and not personal_use.allowed("spotify-via-youtube")


def test_a_spotify_link_on_a_public_install_is_its_oembed_and_plays_on_spotify(public, monkeypatch, tmp_path):
    monkeypatch.setattr(music_links, "_store_path", lambda: tmp_path / "music-links.json")
    asked = []

    def get(url, timeout=15.0):
        asked.append(url)
        if "/embed/" in url:
            raise AssertionError("the embed page is not read on a public install")
        return json.dumps({"title": "Euphoria Morning", "thumbnail_url": "https://i.scdn.co/x.jpg"}).encode()
    monkeypatch.setattr(music_links, "_get", get)
    got = music_links.add("https://open.spotify.com/album/1A2B3C4D5E6F7G8H9I0J1K")
    assert got["title"] == "Euphoria Morning" and got["tracks"] == 0
    from agent_media_core import spotify_remote
    played = []
    monkeypatch.setattr(spotify_remote, "play", lambda uri, device="": (played.append(uri), {"ok": True})[1])
    r = music_links.play(got["id"])
    assert r["ok"] and r["on"] == "spotify" and played == ["spotify:album:1A2B3C4D5E6F7G8H9I0J1K"]
    assert music_links.queue(got["id"], 1) == 0


def test_no_insight_timer_profiles_on_a_public_install(public, monkeypatch):
    calls = []
    monkeypatch.setattr(practice_library, "_get_json", lambda url, timeout=15.0: calls.append(url))
    assert practice_library.teacher_profile("Pura Rasa") == {"name": "Pura Rasa", "profile": None}
    assert practice_library.teacher_places(["Pura Rasa"]) == {} and calls == []
