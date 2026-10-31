"""Saved meditations (meditation_library.py): sources, feeds, the mark."""

import json

import pytest

from agent_media_core import meditation_library as L

FEED = b"""<?xml version="1.0"?>
<rss xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd"><channel>
<title>Calm Talks</title>
<item><title>Letting go</title><pubDate>Fri, 30 Oct 2026</pubDate>
<itunes:duration>22:10</itunes:duration>
<enclosure url="https://cdn.example/letting-go.mp3" type="audio/mpeg"/></item>
<item><title>A video</title><enclosure url="https://cdn.example/v.mp4" type="video/mp4"/></item>
<item><title>Breath</title><enclosure url="https://cdn.example/breath.mp3" type="audio/mpeg"/></item>
</channel></rss>"""


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setenv("MEDIA_MEDITATION_DIR", str(tmp_path / "Meditations"))
    monkeypatch.setattr(L, "_fetch", lambda url, timeout=15.0: FEED)
    monkeypatch.setattr(L, "_yt_title", lambda url: "")


def test_parse_feed_keeps_audio_episodes():
    got = L.parse_feed(FEED)
    assert got["title"] == "Calm Talks"
    assert [e["title"] for e in got["episodes"]] == ["Letting go", "Breath"]
    assert got["episodes"][0]["duration"] == "22:10"


def test_youtube_ids():
    assert L.youtube_id("https://www.youtube.com/watch?v=AAAAAAAAAAA&t=3") == "AAAAAAAAAAA"
    assert L.youtube_id("https://youtu.be/BBBBBBBBBBB") == "BBBBBBBBBBB"
    assert L.youtube_id("https://example.com/feed.xml") is None


def test_a_feed_adds_its_episodes():
    got = L.add("https://example.com/feed.xml")
    assert got == {"added": "podcast", "title": "Calm Talks", "episodes": 2}
    pods = [i for i in L.items() if i["source"] == "podcast"]
    assert [(i["title"], i["show"]) for i in pods] == [("Letting go", "Calm Talks"),
                                                       ("Breath", "Calm Talks")]
    assert L.remove("https://example.com/feed.xml")
    assert not [i for i in L.items() if i["source"] == "podcast"]


def test_a_link_that_is_neither_is_refused(monkeypatch):
    monkeypatch.setattr(L, "_fetch", lambda url, timeout=15.0: b"<html></html>")
    with pytest.raises(ValueError):
        L.add("https://example.com/page")
    with pytest.raises(ValueError):
        L.add("ftp://example.com/x")


def test_files_and_made_ones_are_listed(tmp_path):
    from agent_media_core import meditate
    d = tmp_path / "Meditations"
    (d / "sleep").mkdir(parents=True)
    (d / "sleep" / "Deep_Sleep.m4a").write_bytes(b"x")
    meditate.out_dir().mkdir(parents=True)
    clip = meditate.out_dir() / "meditation-0123456789abcdef.mp3"
    clip.write_bytes(b"x")
    L.remember(clip, "Evening wind-down")
    got = {(i["source"], i["title"]) for i in L.items()}
    assert got == {("file", "Deep Sleep"), ("made", "Evening wind-down")}
    assert L.match("wind down")["id"] == "made:0123456789abcdef"
    assert L.match("deep sleep")["source"] == "file"
    assert L.match("nothing like it") is None
    assert L.remove("made:0123456789abcdef") and not clip.exists()
    assert not L.remove("made:../../etc")


def test_the_mark_finds_what_the_book_plays():
    L.mark_playing("yt:https://www.youtube.com/watch?v=AAAAAAAAAAA")
    assert L.is_marked("/lib/Body scan [AAAAAAAAAAA].m4a")
    assert L.is_marked("https://www.youtube.com/watch?v=AAAAAAAAAAA")
    assert not L.is_marked("/books/dune.mp3")
    L.mark_playing("https://cdn.example/letting-go.mp3?x=1")
    assert L.is_marked("https://cdn.example/letting-go.mp3?x=1")
    assert L.is_marked("/sdcard/Audiobooks/letting-go.mp3")
    from agent_media_core import meditate
    assert meditate.is_meditation("/sdcard/Audiobooks/letting-go.mp3")
    assert not meditate.is_meditation("/books/dune.mp3")


def test_play_refuses_what_is_not_saved():
    assert L.play("/etc/passwd")["ok"] is False
