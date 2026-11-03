"""Spotify albums, playlists and songs, and YouTube links, saved for the Music tab (music_links.py).

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


def test_only_spotify_and_youtube_links():
    with pytest.raises(ValueError):
        music_links.add("https://soundcloud.com/someone/a-song")


def test_parse_youtube_links():
    p = music_links.parse_youtube
    assert p("https://youtu.be/8r31DFrFs5A?si=Fozv6bwCFWSwC8uM") == ("video", "8r31DFrFs5A")
    assert p("https://www.youtube.com/watch?v=8r31DFrFs5A&list=PL0123456789") == ("video", "8r31DFrFs5A")
    assert p("https://m.youtube.com/watch?feature=share&v=8r31DFrFs5A") == ("video", "8r31DFrFs5A")
    assert p("https://music.youtube.com/watch?v=8r31DFrFs5A") == ("video", "8r31DFrFs5A")
    assert p("https://www.youtube.com/shorts/8r31DFrFs5A") == ("video", "8r31DFrFs5A")
    assert p("https://www.youtube.com/playlist?list=PLabcdefghij123") == ("playlist", "PLabcdefghij123")
    assert p("https://www.youtube.com/@MassiveAttackVEVO") is None
    assert p("https://example.com/watch?v=8r31DFrFs5A") is None
    # The share sheet's Play it keeps its own path for a YouTube link.
    assert not music_links.is_music_link("https://youtu.be/8r31DFrFs5A")


def _youtube_oembed(monkeypatch):
    asked = []

    def get(url, timeout=15.0):
        asked.append(url)
        return json.dumps({"title": "Massive Attack - The Spoils ft. Hope Sandoval",
                           "author_name": "MassiveAttackVEVO",
                           "thumbnail_url": "https://i.ytimg.com/vi/8r31DFrFs5A/hqdefault.jpg"}).encode()
    monkeypatch.setattr(music_links, "_get", get)
    return asked


def test_a_youtube_video_lands_in_the_albums_list(monkeypatch):
    asked = _youtube_oembed(monkeypatch)
    got = music_links.add("https://youtu.be/8r31DFrFs5A?si=Fozv6bwCFWSwC8uM")
    assert got == {"added": "music", "id": "album:youtube-video-8r31DFrFs5A",
                   "title": "Massive Attack - The Spoils ft. Hope Sandoval",
                   "artist": "MassiveAttackVEVO", "tracks": 0}
    assert asked and asked[0].startswith("https://www.youtube.com/oembed?")
    [row] = music_recent.listed(kind="music", which="albums")
    assert row["source"] == "youtube" and row["link"] == "https://www.youtube.com/watch?v=8r31DFrFs5A"
    assert row["art"] == "https://i.ytimg.com/vi/8r31DFrFs5A/hqdefault.jpg"
    assert music_links.spotify_uri(row["id"]) is None
    # Saved again: one row, not two.
    music_links.add("https://www.youtube.com/watch?v=8r31DFrFs5A")
    assert len(music_links.items()) == 1


def test_a_youtube_row_plays_through_the_music_player(monkeypatch):
    _youtube_oembed(monkeypatch)
    got = music_links.add("https://www.youtube.com/playlist?list=PLabcdefghij123")
    ran = []

    class R:
        returncode, stdout, stderr = 0, "", ""

    import subprocess
    monkeypatch.setattr(subprocess, "run", lambda argv, **kw: ran.append(argv) or R())
    assert music_links.play(got["id"]) == {"ok": True, "title": got["title"], "on": "music"}
    assert ran[0][-3:] == ["music", "play", "https://www.youtube.com/playlist?list=PLabcdefghij123"]


def test_a_youtube_row_refused_here_gives_its_link(monkeypatch):
    _youtube_oembed(monkeypatch)
    got = music_links.add("https://youtu.be/8r31DFrFs5A")

    class R:
        returncode, stdout = 1, ""
        stderr = "YouTube can't be streamed on this server, and it couldn't be downloaded"

    import subprocess
    monkeypatch.setattr(subprocess, "run", lambda argv, **kw: R())
    r = music_links.play(got["id"])
    assert not r["ok"] and r["code"] == "youtube" and r["link"] == "https://www.youtube.com/watch?v=8r31DFrFs5A"
    assert "open it in YouTube" in r["error"]
