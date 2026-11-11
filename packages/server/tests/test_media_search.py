"""`GET /media/search` (§6.46a, media_search.py): one search across the Media
tabs. Every outside source is a stub; Trakt is not set up."""

from __future__ import annotations

import pytest

from agent_media_core import music_bed_favourites as fav
from agent_media_core import music_discover, music_recent, podcast_discover, practice_library

from test_contract import AUTH, call, server, signed_in, typed  # noqa: F401


@pytest.fixture()
def stubs(monkeypatch):
    monkeypatch.setattr(fav, "_may_search", lambda: True)
    monkeypatch.setattr(fav, "search", lambda q: [{"id": "kerala00000", "title": "Kerala", "artist": "Bonobo", "dur": 230}])
    monkeypatch.setattr(podcast_discover, "_get", lambda url, timeout=15.0: (200, {"results": [
        {"collectionId": 1, "collectionName": "Bonobo Talks", "feedUrl": "https://f/x.xml"}]}))

    def listed(kind="music", which="recent", store=None, limit=40, q=""):
        rows = {("music", "recent"): [{"uri": "yt:a", "title": "Bonobo - Cirrus"}],
                ("book", "recent"): [], ("book", "all"): [{"uri": "/b.m4b", "title": "Bonobo's Book"}],
                ("podcast", "recent"): []}
        return [r for r in rows.get((kind, which), []) if music_recent.matches(r, q)]
    monkeypatch.setattr(music_recent, "listed", listed)
    monkeypatch.setattr(practice_library, "items", lambda: [{"id": "yt:p", "title": "Bonobo yoga flow"}, {"id": "yt:q", "title": "Other"}])
    music_discover.clear_cache()
    podcast_discover.clear_cache()
    yield
    music_discover.clear_cache()
    podcast_discover.clear_cache()


def test_one_search_every_tab(server, signed_in, stubs):
    res, obj = call(server, "GET", "/media/search?q=bonobo", headers=AUTH)
    assert res.status == 200, obj
    g = obj["groups"]
    assert [r["title"] for r in g["music"]["mine"]] == ["Bonobo - Cirrus"]
    assert [r["title"] for r in g["music"]["found"]] == ["Kerala"]
    assert [r["title"] for r in g["book"]["mine"]] == ["Bonobo's Book"]
    assert [r["title"] for r in g["podcast"]["found"]] == ["Bonobo Talks"]
    assert [r["title"] for r in g["practice"]["mine"]] == ["Bonobo yoga flow"]
    assert [r["title"] for r in g["practice"]["found"]] == ["Kerala"]
    assert g["movie"] == {"mine": [], "found": []} and g["tv"] == {"mine": [], "found": []}
    res, _ = call(server, "GET", "/media/search?q=b", headers=AUTH)
    assert res.status == 400


def test_media_search_is_gated(server, stubs):
    res, _ = call(server, "GET", "/media/search?q=bonobo")
    assert res.status in (401, 403)
    res, _ = call(server, "OPTIONS", "/media/search")
    assert res.status in (200, 204)
