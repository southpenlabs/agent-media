"""Spotify albums, playlists and songs saved for the Music tab (music_links.py).

Nothing is fetched or played: Spotify's page is a fixture and `read` stands in."""

from __future__ import annotations

import json

import pytest

from agent_media_core import music_links, music_recent

PAGE = '<html><script id="__NEXT_DATA__" type="application/json">' + json.dumps({"props": {"pageProps": {"state": {"data": {"entity": {
    "type": "album", "name": "The Heart Chronicles", "subtitle": "Eli Poz",
    "trackList": [{"title": "Just a Little Crush", "subtitle": "Eli Poz"},
                  {"title": "First Love", "subtitle": "Eli Poz, Someone Else"}]}}}}}}) + "</script></html>"


@pytest.fixture
def _read(monkeypatch):
    got = {**music_links.parse_embed(PAGE), "art": "https://i.scdn.co/image/x"}
    monkeypatch.setattr(music_links, "read", lambda kind, sid: got)


def test_parse_links():
    assert music_links.parse("https://open.spotify.com/album/1qiw9zXdtK5Xidv4j8W28Y?si=D2v&utm_source=copy-link") == ("album", "1qiw9zXdtK5Xidv4j8W28Y")
    assert music_links.parse("https://open.spotify.com/intl-de/playlist/37i9dQZF1DXcBWIGoYBM5M") == ("playlist", "37i9dQZF1DXcBWIGoYBM5M")
    assert music_links.parse("https://open.spotify.com/episode/1qiw9zXdtK5Xidv4j8W28Y") is None
    assert music_links.parse("https://example.com/album/1qiw9zXdtK5Xidv4j8W28Y") is None


def test_the_embed_page_gives_the_tracks():
    got = music_links.parse_embed(PAGE)
    assert got["title"] == "The Heart Chronicles" and got["artist"] == "Eli Poz"
    assert got["tracks"] == [{"artist": "Eli Poz", "title": "Just a Little Crush"},
                             {"artist": "Eli Poz", "title": "First Love"}]


def test_an_album_lands_in_the_albums_list(_read):
    got = music_links.add("https://open.spotify.com/album/1qiw9zXdtK5Xidv4j8W28Y?si=x")
    assert got == {"added": "music", "id": "album:spotify-album-1qiw9zXdtK5Xidv4j8W28Y",
                   "title": "The Heart Chronicles", "artist": "Eli Poz", "tracks": 2}
    [row] = music_recent.listed(kind="music", which="albums")
    assert row["uri"] == "album:spotify-album-1qiw9zXdtK5Xidv4j8W28Y" and row["tracks"] == 2
    assert row["artist"] == "Eli Poz" and row["art"] == "https://i.scdn.co/image/x"
    assert music_links.remove(row["id"]) and music_links.items() == []


def test_a_track_is_found_once_and_kept(_read, monkeypatch):
    music_links.add("https://open.spotify.com/album/1qiw9zXdtK5Xidv4j8W28Y")
    asked = []
    monkeypatch.setattr(music_links, "_vid", lambda t: asked.append(t["title"]) or "zi2QJ-4zOyc")
    key = "spotify-album-1qiw9zXdtK5Xidv4j8W28Y"
    assert music_links._resolve(key, 0) == "zi2QJ-4zOyc"
    assert music_links._resolve(key, 0) == "zi2QJ-4zOyc"
    assert asked == ["Just a Little Crush"]


def test_only_spotify_links():
    with pytest.raises(ValueError):
        music_links.add("https://www.youtube.com/watch?v=AAAAAAAAAAA")
