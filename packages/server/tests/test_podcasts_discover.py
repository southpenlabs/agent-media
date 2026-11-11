"""`/podcasts/discover` and `/podcasts/show` (§6.9a "Podcasts: search,
browse, follow", podcast_discover.py). Apple's directory and chart are stubs
at podcast_discover._get, the feed at practice_library._fetch; nothing plays
(music._cli is faked)."""

from __future__ import annotations

import urllib.parse

import pytest

from agent_media_core import podcast_discover as P
from agent_media_core import practice_library
from agent_media_server import music

from test_contract import AUTH, audio_host, call, server, signed_in, typed  # noqa: F401

FEED = "https://feeds.example.com/history.xml"
_ITEMS = "".join(
    f'<item><title>Ep {n}</title><enclosure url="https://cdn.example.com/{n}.mp3" type="audio/mpeg"/>'
    f'<pubDate>{"Mon, 09 Nov 2026" if n == 70 else "Mon, 26 Oct 2026" if n == 69 else "Mon, 12 Oct 2026" if n == 68 else "Sun, 01 Jan 2023"} 00:00:00 GMT</pubDate>'
    f'<itunes:duration>{"1:30:00" if n == 70 else "3600"}</itunes:duration>'
    f'<description>&lt;p&gt;{"Rome burns" if n == 70 else "Other things"}&lt;/p&gt;</description></item>'
    for n in range(70, 47, -1))
RSS = ('<?xml version="1.0"?><rss xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd"><channel>'
       '<title>Hardcore History</title><itunes:author>Dan Carlin</itunes:author><link>https://www.dancarlin.com</link>'
       '<itunes:image href="https://a/feed.jpg"/><itunes:category text="History"/><itunes:summary>Was history ever this good?</itunes:summary>'
       + _ITEMS + "</channel></rss>").encode()


@pytest.fixture()
def apple(monkeypatch):
    asked = []

    def get(url, timeout=15.0):
        asked.append(url)
        u = urllib.parse.urlsplit(url)
        q = urllib.parse.parse_qs(u.query)
        if u.path == "/search" and q.get("entity") == ["podcastEpisode"]:
            return 200, {"results": [{"trackId": 9, "trackName": "Ep 70", "collectionName": "Dan Carlin's Hardcore History",
                                      "collectionId": 173001861, "feedUrl": FEED, "episodeUrl": "https://cdn.example.com/70.mp3",
                                      "releaseDate": "2026-11-09T00:00:00Z", "trackTimeMillis": 5400000,
                                      "description": "<b>Rome</b> burns"}]}
        if u.path == "/search" and q.get("attribute") == ["authorTerm"]:
            return 200, {"results": [{"collectionId": 173001861, "collectionName": "Dan Carlin's Hardcore History", "artistName": "Dan Carlin"},
                                     {"collectionId": 5, "collectionName": "Common Sense", "artistName": "Dan Carlin"},
                                     {"collectionId": 6, "collectionName": "Not His", "artistName": "Dan Carlinsky"}]}
        if u.path == "/search":
            assert q["media"] == ["podcast"] and q["country"] == ["au"]
            return 200, {"results": [{"collectionId": 173001861, "genreIds": ["1487", "26"], "collectionName": "Dan Carlin's Hardcore History",
                                      "artistName": "Dan Carlin", "feedUrl": FEED, "artworkUrl600": "https://a/600.jpg",
                                      "primaryGenreName": "History", "trackCount": 70}]}
        if u.path == "/lookup":
            return 200, {"results": [{"collectionId": 173001861, "collectionName": "Dan Carlin's Hardcore History",
                                      "artistName": "Dan Carlin", "feedUrl": FEED, "artworkUrl600": "https://a/600.jpg"}]}
        if "/rss/toppodcasts/" in u.path:
            assert u.path.startswith("/au/rss/toppodcasts/limit=100")
            genre = "genre=1487" in u.path
            return 200, {"feed": {"entry": [{"im:name": {"label": "History Show" if genre else "Top Show"},
                                             "im:artist": {"label": "Someone"},
                                             "im:image": [{"label": "https://a/x/55x55bb.png"}, {"label": "https://a/x/170x170bb.png"}],
                                             "id": {"attributes": {"im:id": "111" if genre else "222"}},
                                             "category": {"attributes": {"label": "History" if genre else "Comedy"}}}]}}
        return 404, None
    monkeypatch.setattr(P, "_get", get)
    monkeypatch.setattr(P, "_fetch_feed", lambda url: RSS)
    P.clear_cache()
    yield asked
    P.clear_cache()


def test_search_top_and_genre(server, signed_in, apple):
    res, obj = call(server, "GET", "/podcasts/discover?list=search&q=hardcore%20history", headers=AUTH)
    assert res.status == 200, obj
    assert obj["items"][0]["title"] == "Dan Carlin's Hardcore History" and obj["items"][0]["feed"] == FEED
    assert obj["items"][0]["following"] is False and obj["items"][0]["genre_id"] == "1487"
    assert obj["episodes"][0]["title"] == "Ep 70" and obj["episodes"][0]["description"] == "Rome burns"
    assert obj["episodes"][0]["duration_s"] == 5400 and obj["episodes"][0]["feed"] == FEED
    _, obj = call(server, "GET", "/podcasts/discover?list=author&q=Dan%20Carlin", headers=AUTH)
    assert [i["title"] for i in obj["items"]] == ["Dan Carlin's Hardcore History", "Common Sense"]
    _, obj = call(server, "GET", "/podcasts/discover?list=top", headers=AUTH)
    assert obj["items"][0]["title"] == "Top Show" and obj["items"][0]["art"] == "https://a/x/600x600bb.jpg"
    _, obj = call(server, "GET", "/podcasts/discover?list=top&genre=1487", headers=AUTH)
    assert obj["items"][0]["title"] == "History Show" and {"id": "1487", "name": "History"} in obj["genres"]
    for bad in ("list=nope", "list=top&genre=9", "list=search&q=a", "list=top&page=x"):
        res, _ = call(server, "GET", f"/podcasts/discover?{bad}", headers=AUTH)
        assert res.status == 400, bad


def test_a_show_followed_and_an_episode_played(server, signed_in, audio_host, apple, monkeypatch):
    ran = []
    monkeypatch.setattr(music, "_cli", lambda argv, *_: (ran.append(argv), (True, ""))[1])
    monkeypatch.setattr(music, "_run", lambda argv, *_: (ran.append(argv), (True, ""))[1])
    monkeypatch.setattr(music, "_status", lambda: {"backend": None})
    res, obj = call(server, "GET", "/podcasts/show?itunes=173001861", headers=AUTH)
    assert res.status == 200, obj
    assert obj["show"]["feed"] == FEED and obj["show"]["following"] is False
    sh = obj["show"]
    assert (sh["author"], sh["description"], sh["link"], sh["categories"], sh["episodes"]) == (
        "Dan Carlin", "Was history ever this good?", "https://www.dancarlin.com", ["History"], 23)
    assert sh["last_episode"].startswith("Mon, 09 Nov 2026") and sh["every_days"] == 14.0
    assert [e["title"] for e in obj["episodes"][:2]] == ["Ep 70", "Ep 69"] and len(obj["episodes"]) == 20 and obj["more"]
    assert obj["episodes"][0]["duration_s"] == 5400 and obj["episodes"][0]["description"] == "Rome burns"
    _, more = call(server, "GET", "/podcasts/show?itunes=173001861&page=2", headers=AUTH)
    assert [e["title"] for e in more["episodes"]] == ["Ep 50", "Ep 49", "Ep 48"] and not more["more"]
    _, found = call(server, "GET", "/podcasts/show?itunes=173001861&q=rome", headers=AUTH)
    assert [e["title"] for e in found["episodes"]] == ["Ep 70"]
    res, obj = call(server, "POST", "/podcasts/show", {"action": "follow", "feed": FEED, "title": "Hardcore History",
                                                        "itunes": "173001861"}, AUTH)
    assert obj == {"ok": True, "following": True}
    _, obj = call(server, "GET", "/podcasts/discover?list=following", headers=AUTH)
    assert [i["title"] for i in obj["items"]] == ["Hardcore History"]
    _, obj = call(server, "GET", "/podcasts/discover?list=new", headers=AUTH)
    assert [(e["title"], e["show"]) for e in obj["episodes"]] == [("Ep 70", "Hardcore History"), ("Ep 69", "Hardcore History"), ("Ep 68", "Hardcore History")]
    _, obj = call(server, "GET", "/podcasts/discover?list=search&q=hardcore", headers=AUTH)
    assert obj["items"][0]["following"] is True
    res, obj = call(server, "POST", "/podcasts/show", {"action": "play", "feed": FEED, "url": "https://cdn.example.com/70.mp3"}, AUTH)
    assert res.status == 200, obj
    assert obj["episode"]["title"] == "Ep 70" and ran[-1][-1] == "https://cdn.example.com/70.mp3"
    _, saved = call(server, "GET", "/music/recent?kind=podcast&list=saved", headers=AUTH)
    assert saved["items"][0]["title"] == "Ep 70" and saved["items"][0]["author"] == "Hardcore History"
    res, _ = call(server, "POST", "/podcasts/show", {"action": "play", "feed": FEED, "url": "https://evil/x.mp3"}, AUTH)
    assert res.status == 400
    res, obj = call(server, "POST", "/podcasts/show", {"action": "unfollow", "feed": FEED}, AUTH)
    assert obj["following"] is False
    music._reset_cache(forget=True)


def test_podcasts_are_gated(server, apple):
    res, _ = call(server, "GET", "/podcasts/discover?list=top")
    assert res.status in (401, 403)
    res, _ = call(server, "OPTIONS", "/podcasts/show")
    assert res.status in (200, 204)
