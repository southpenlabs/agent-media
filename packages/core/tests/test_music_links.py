"""Spotify albums, playlists and songs saved for the Music tab (music_links.py).

Nothing is fetched or played: the spotify-via-youtube add-on is a stand-in
(the add-on tests its own reading and playing)."""

from __future__ import annotations

import json

import pytest

from agent_media_core import music_links, music_recent, personal_use

GOT = {"title": "The Heart Chronicles", "artist": "Eli Poz", "art": "https://i.scdn.co/image/x",
       "tracks": [{"artist": "Eli Poz", "title": "Just a Little Crush"}, {"artist": "Eli Poz", "title": "First Love"}]}


class _Addon:
    def __init__(self):
        self.played = []

    def read(self, kind, sid):
        return dict(GOT)

    def play(self, key):
        self.played.append(key)
        return {"ok": True, "tracks": 2}

    def queue(self, key, start):
        return 1


@pytest.fixture
def _read():
    addon = _Addon()
    personal_use.register("spotify-via-youtube", addon)
    return addon


def test_parse_links():
    assert music_links.parse("https://open.spotify.com/album/1qiw9zXdtK5Xidv4j8W28Y?si=D2v&utm_source=copy-link") == ("album", "1qiw9zXdtK5Xidv4j8W28Y")
    assert music_links.parse("https://open.spotify.com/intl-de/playlist/37i9dQZF1DXcBWIGoYBM5M") == ("playlist", "37i9dQZF1DXcBWIGoYBM5M")
    assert music_links.parse("https://open.spotify.com/episode/1qiw9zXdtK5Xidv4j8W28Y") is None
    assert music_links.parse("https://example.com/album/1qiw9zXdtK5Xidv4j8W28Y") is None


def test_an_album_lands_in_the_albums_list(_read):
    got = music_links.add("https://open.spotify.com/album/1qiw9zXdtK5Xidv4j8W28Y?si=x")
    assert got == {"added": "music", "id": "album:spotify-album-1qiw9zXdtK5Xidv4j8W28Y",
                   "title": "The Heart Chronicles", "artist": "Eli Poz", "tracks": 2}
    [row] = music_recent.listed(kind="music", which="albums")
    assert row["uri"] == "album:spotify-album-1qiw9zXdtK5Xidv4j8W28Y" and row["tracks"] == 2
    assert row["artist"] == "Eli Poz" and row["art"] == "https://i.scdn.co/image/x"
    assert music_links.remove(row["id"]) and music_links.items() == []


def test_the_addon_plays_it_where_installed(_read):
    music_links.add("https://open.spotify.com/album/1qiw9zXdtK5Xidv4j8W28Y")
    key = "album:spotify-album-1qiw9zXdtK5Xidv4j8W28Y"
    assert music_links.play(key)["ok"] and _read.played == [key]
    assert music_links.queue(key, 1) == 1


def test_without_the_addon_it_plays_on_spotify(monkeypatch):
    personal_use.register("spotify-via-youtube", None)
    monkeypatch.setattr(music_links, "_get", lambda url, timeout=15.0: json.dumps({"title": "Euphoria Morning", "thumbnail_url": None}).encode())
    got = music_links.add("https://open.spotify.com/album/1A2B3C4D5E6F7G8H9I0J1K")
    assert got["tracks"] == 0
    from agent_media_core import spotify_remote
    monkeypatch.setattr(spotify_remote, "play", lambda uri, device="": {"ok": True})
    assert music_links.play(got["id"])["on"] == "spotify" and music_links.queue(got["id"], 1) == 0


def test_only_spotify_links():
    with pytest.raises(ValueError):
        music_links.add("https://www.youtube.com/watch?v=AAAAAAAAAAA")
