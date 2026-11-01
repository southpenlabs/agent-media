"""A podcast episode's audio from a shared link (podcast_resolve.py)."""

from __future__ import annotations

import json

import pytest

from agent_media_core import podcast_resolve as R

SEARCH = {"results": [
    {"trackName": "354 - The Boy Who Cried Hum", "collectionName": "Hamish & Andy",
     "episodeUrl": "https://traffic.example/354.mp3", "releaseDate": "2026-10-21T13:00:00Z", "trackTimeMillis": 2668000},
    {"trackName": "355 - Can We Still Pass a Driving Test?", "collectionName": "Hamish & Andy",
     "episodeUrl": "https://traffic.example/355.mp3", "releaseDate": "2026-10-28T13:00:00Z", "trackTimeMillis": 2399000,
     "artworkUrl600": "https://art.example/600.jpg"}]}


@pytest.fixture(autouse=True)
def _on(monkeypatch):
    monkeypatch.setenv("MEDIA_PODCAST_RESOLVE", "1")


def _fake(pages):
    def get(url, timeout=10.0):
        for prefix, body in pages.items():
            if url.startswith(prefix):
                if isinstance(body, Exception):
                    raise body
                return body if isinstance(body, bytes) else json.dumps(body).encode()
        raise OSError("no such page")
    return get


def test_a_page_this_host_cannot_read_is_found_by_its_words(monkeypatch):
    monkeypatch.setattr(R, "_get", _fake({"https://www.podcastoneaustralia.com.au": OSError("403"),
                                          R.ITUNES_SEARCH: SEARCH}))
    got = R.episode("https://www.podcastoneaustralia.com.au/episode/abc",
                    "355 - Can We Still Pass a Driving Test? - Hamish & Andy https://www.podcastoneaustralia.com.au/episode/abc")
    assert (got["audio"], got["show"], got["duration_s"], got["via"]) == ("https://traffic.example/355.mp3", "Hamish & Andy", 2399, "search")
    assert got["published"] > 0 and got["art"] == "https://art.example/600.jpg"


def test_a_weak_match_is_not_taken(monkeypatch):
    monkeypatch.setattr(R, "_get", _fake({"https://pca.st": OSError("no"), R.ITUNES_SEARCH: SEARCH}))
    assert R.episode("https://pca.st/episode/x", "Driving lessons for cats") is None


def test_a_pages_own_audio_wins(monkeypatch):
    page = b'<html><head><meta property="og:title" content="Ep 9"><meta property="og:audio" content="https://cdn.example/ep9.mp3"></head></html>'
    monkeypatch.setattr(R, "_get", _fake({"https://show.example": page}))
    got = R.episode("https://show.example/ep/9", "")
    assert (got["audio"], got["title"], got["via"]) == ("https://cdn.example/ep9.mp3", "Ep 9", "page")


def test_an_audio_link_is_itself_and_search_can_be_off(monkeypatch):
    assert R.episode("https://cdn.example/x.mp3?t=1", "")["via"] == "link"
    monkeypatch.setattr(R, "_get", _fake({"https://news.example": b"<html></html>", R.ITUNES_SEARCH: SEARCH}))
    assert R.episode("https://news.example/story", "355 Can We Still Pass a Driving Test", search=False) is None


def test_what_looks_like_a_podcast():
    assert R.looks_like_podcast("https://www.podcastoneaustralia.com.au/episode/x")
    assert R.looks_like_podcast("https://pca.st/abc")
    assert not R.looks_like_podcast("https://www.abc.net.au/news/story")
