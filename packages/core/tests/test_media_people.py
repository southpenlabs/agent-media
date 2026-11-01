"""Who made it: artists, authors and shows (media_people.py)."""

from __future__ import annotations

import json

import pytest

from agent_media_core import media_people as P


@pytest.fixture(autouse=True)
def _iso(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("MEDIA_PEOPLE", "1")
    monkeypatch.setattr(P.time, "sleep", lambda s: None)


def _fake(monkeypatch, answers):
    calls = []

    def get(url, timeout=12.0):
        calls.append(url)
        for prefix, body in answers.items():
            if url.startswith(prefix):
                return body if isinstance(body, bytes) else json.dumps(body).encode()
        raise OSError("no such page")
    monkeypatch.setattr(P, "_get", get)
    return calls


def test_an_artist_is_the_exact_name_with_its_linked_wikipedia_page(monkeypatch):
    calls = _fake(monkeypatch, {
        f"{P.MB}/artist?": {"artists": [{"id": "x", "name": "Chris Cornell Experience"}, {"id": "cc", "name": "Chris Cornell"}]},
        f"{P.MB}/artist/cc": {"id": "cc", "name": "Chris Cornell", "type": "Person", "life-span": {"begin": "1964-07-20", "end": "2017-05-18"},
                              "area": {"name": "United States"}, "begin-area": {"name": "Seattle"},
                              "relations": [{"type": "wikidata", "url": {"resource": "https://www.wikidata.org/wiki/Q1"}},
                                            {"type": "official homepage", "url": {"resource": "https://chriscornell.com"}},
                                            {"type": "official homepage", "url": {"resource": "https://other.example"}}]},
        "https://www.wikidata.org/": {"entities": {"Q1": {"sitelinks": {"enwiki": {"title": "Chris Cornell"}}}}},
        P.WIKI_SUMMARY: {"extract": "An American musician.", "description": "American musician",
                         "thumbnail": {"source": "https://img.example/cc.jpg"}, "content_urls": {"desktop": {"page": "https://en.wikipedia.org/wiki/Chris_Cornell"}}}})
    got = P.profile("artist", "Chris Cornell")["profile"]
    assert (got["region"], got["facts"], got["photo"]) == ("Seattle, United States", ["born 1964, died 2017"], "https://img.example/cc.jpg")
    assert [x["label"] for x in got["links"]] == ["Website", "Wikipedia", "MusicBrainz"]
    n = len(calls)
    assert P.profile("artist", "Chris Cornell")["profile"] == got and len(calls) == n, "kept, not asked again"


def test_an_author_page_must_say_a_writer(monkeypatch):
    _fake(monkeypatch, {P.WIKI_SUMMARY: {"extract": "A footballer.", "description": "English footballer"}})
    monkeypatch.setattr(P, "_abs_author", lambda name: None)
    assert P.profile("author", "John Smith")["profile"] is None
    monkeypatch.setattr(P, "_abs_author", lambda name: "Writes urban fantasy.")
    got = P.profile("author", "M. D. Massey", fresh=True)["profile"]
    assert (got["source"], got["description"]) == ("audiobookshelf", "Writes urban fantasy.")


def test_a_show_from_the_directory_with_its_feeds_words(monkeypatch):
    feed = b"<rss><channel><title>H&amp;A</title><description>&lt;p&gt;Two mates.&lt;/p&gt;</description></channel></rss>"
    _fake(monkeypatch, {P.ITUNES_SEARCH: {"results": [{"collectionName": "Hamish & Andy", "artistName": "LiSTNR",
                                                       "primaryGenreName": "Comedy", "trackCount": 358, "feedUrl": "https://feed.example/ha",
                                                       "artworkUrl600": "https://art.example/600.jpg", "collectionViewUrl": "https://podcasts.apple.com/x"}]},
                        "https://feed.example/ha": feed})
    got = P.profile("show", "Hamish & Andy")["profile"]
    assert got["facts"] == ["by LiSTNR", "Comedy", "358 episodes"] and got["description"] == "Two mates."
    assert got["links"] == [{"label": "Apple Podcasts", "url": "https://podcasts.apple.com/x"}]


def test_off_or_unknown_kind_asks_nothing(monkeypatch):
    calls = _fake(monkeypatch, {})
    monkeypatch.setenv("MEDIA_PEOPLE", "0")
    assert P.profile("artist", "Anyone")["profile"] is None
    assert P.profile("painter", "Anyone")["profile"] is None and calls == []
