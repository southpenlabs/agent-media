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
RSS = b"""<?xml version="1.0"?><rss><channel><title>Hardcore History</title>
<item><title>Ep 70</title><enclosure url="https://cdn.example.com/70.mp3" type="audio/mpeg"/><pubDate>Mon, 09 Nov 2026 00:00:00 GMT</pubDate></item>
<item><title>Ep 69</title><enclosure url="https://cdn.example.com/69.mp3" type="audio/mpeg"/></item></channel></rss>"""


@pytest.fixture()
def apple(monkeypatch):
    asked = []

    def get(url, timeout=15.0):
        asked.append(url)
        u = urllib.parse.urlsplit(url)
        q = urllib.parse.parse_qs(u.query)
        if u.path == "/search":
            assert q["media"] == ["podcast"] and q["country"] == ["au"]
            return 200, {"results": [{"collectionId": 173001861, "collectionName": "Dan Carlin's Hardcore History",
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
    monkeypatch.setattr(practice_library, "_fetch", lambda url, timeout=15.0: RSS)
    P.clear_cache()
    yield asked
    P.clear_cache()


def test_search_top_and_genre(server, signed_in, apple):
    res, obj = call(server, "GET", "/podcasts/discover?list=search&q=hardcore%20history", headers=AUTH)
    assert res.status == 200, obj
    assert obj["items"][0]["title"] == "Dan Carlin's Hardcore History" and obj["items"][0]["feed"] == FEED
    assert obj["items"][0]["following"] is False
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
    assert [e["title"] for e in obj["episodes"]] == ["Ep 70", "Ep 69"]
    res, obj = call(server, "POST", "/podcasts/show", {"action": "follow", "feed": FEED, "title": "Hardcore History",
                                                        "itunes": "173001861"}, AUTH)
    assert obj == {"ok": True, "following": True}
    _, obj = call(server, "GET", "/podcasts/discover?list=following", headers=AUTH)
    assert [i["title"] for i in obj["items"]] == ["Hardcore History"]
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
