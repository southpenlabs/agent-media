"""A link into a Media tab: `GET`/`POST /media/links` (server-contract.md §6.46).

Over real HTTP to an in-process canvas (test_contract's rig). Nothing is
downloaded (MEDIA_PODCAST_LINK_FETCH=0, conftest) or played.
"""

from __future__ import annotations

import pytest

from test_contract import AUTH, call, server, shelf, signed_in, typed  # noqa: F401


@pytest.fixture(autouse=True)
def _dirs(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setenv("MEDIA_PODCAST_LINK_DIR", str(tmp_path / "links"))
    monkeypatch.setenv("MEDIA_PRACTICE_DIR", str(tmp_path / "Practice"))


def test_without_a_credential_is_refused(server):
    assert call(server, "GET", "/media/links")[0].status != 200


def test_the_tabs_that_take_a_link(server, shelf, signed_in):
    res, obj = call(server, "GET", "/media/links", None, AUTH)
    assert res.status == 200, obj
    assert [t["id"] for t in obj["to"]] == ["podcast", "practice", "book", "music"]


def test_a_podcast_link_lands_in_the_saved_list(server, shelf, signed_in):
    res, obj = call(server, "POST", "/media/links",
                    {"url": "https://youtu.be/AAAAAAAAAAA", "to": "podcast", "title": "A talk"}, AUTH)
    assert res.status == 200, obj
    assert obj["added"] == "podcast" and obj["fetching"] is False
    res, obj = call(server, "GET", "/music/recent?kind=podcast&list=saved", None, AUTH)
    assert res.status == 200, obj
    assert "saved" in obj["lists"]
    item = obj["items"][0]
    assert item["title"] == "A talk" and item["downloaded"] is False
    assert item["uri"] == "yt:https://www.youtube.com/watch?v=AAAAAAAAAAA"
    res, obj = call(server, "POST", "/media/links",
                    {"action": "remove", "id": "podcast-link:AAAAAAAAAAA"}, AUTH)
    assert res.status == 200
    _, obj = call(server, "GET", "/music/recent?kind=podcast&list=saved", None, AUTH)
    assert obj["items"] == []


def test_a_practice_link_goes_to_practice(server, shelf, signed_in):
    res, obj = call(server, "POST", "/media/links",
                    {"url": "https://youtu.be/BBBBBBBBBBB", "to": "practice", "title": "Body scan"}, AUTH)
    assert res.status == 200, obj
    assert obj["added"] == "youtube"


def test_a_podcast_link_must_be_a_video(server, shelf, signed_in):
    res, obj = call(server, "POST", "/media/links",
                    {"url": "https://example.com/feed.xml", "to": "podcast"}, AUTH)
    assert res.status == 400 and not obj["ok"]


def test_an_unknown_tab_is_400(server, shelf, signed_in):
    res, obj = call(server, "POST", "/media/links",
                    {"url": "https://youtu.be/AAAAAAAAAAA", "to": "fridge"}, AUTH)
    assert res.status == 400


def test_a_spotify_album_goes_to_music(server, shelf, signed_in, monkeypatch):
    from agent_media_core import music_links
    monkeypatch.setattr(music_links, "read", lambda kind, sid: {
        "title": "The Heart Chronicles", "artist": "Eli Poz", "art": None,
        "tracks": [{"artist": "Eli Poz", "title": "First Love"}]})
    res, obj = call(server, "POST", "/media/links",
                    {"url": "https://open.spotify.com/album/1qiw9zXdtK5Xidv4j8W28Y?si=x", "to": "music"}, AUTH)
    assert res.status == 200, obj
    assert obj["added"] == "music" and obj["tracks"] == 1
    _, obj = call(server, "GET", "/music/recent?list=albums", None, AUTH)
    assert [i["title"] for i in obj["items"]] == ["The Heart Chronicles"]
    res, obj = call(server, "POST", "/media/links",
                    {"action": "remove", "id": "album:spotify-album-1qiw9zXdtK5Xidv4j8W28Y"}, AUTH)
    assert res.status == 200


def test_playing_on_spotify_signed_out_says_how(server, shelf, signed_in, monkeypatch):
    from agent_media_core import music_links
    monkeypatch.setattr(music_links, "read", lambda kind, sid: {
        "title": "The Heart Chronicles", "artist": "Eli Poz", "art": None,
        "tracks": [{"artist": "Eli Poz", "title": "First Love"}]})
    call(server, "POST", "/media/links",
         {"url": "https://open.spotify.com/album/1qiw9zXdtK5Xidv4j8W28Y", "to": "music"}, AUTH)
    res, obj = call(server, "POST", "/music",
                    {"action": "spotify", "uri": "album:spotify-album-1qiw9zXdtK5Xidv4j8W28Y"}, AUTH)
    assert res.status == 409, obj
    assert obj["code"] == "signed_out" and obj["link"] == "https://open.spotify.com/album/1qiw9zXdtK5Xidv4j8W28Y"
    res, obj = call(server, "POST", "/music", {"action": "spotify", "uri": "album:nope"}, AUTH)
    assert res.status == 404
