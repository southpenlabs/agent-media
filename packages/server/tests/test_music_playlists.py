"""Playlists (music_playlists; `POST /music {"action": "playlist"}`, the
Music tab's `playlists` list). Nothing reaches a player or YouTube: the
`media music` runner and the name search are faked at the module's seams."""

from __future__ import annotations

import pytest

from agent_media_core import music_discover, music_playlists as mp
from agent_media_server import music

from test_contract import AUTH, audio_host, call, keys, server, signed_in, typed  # noqa: F401
from test_music_route import NOW, _no_phone  # noqa: F401


@pytest.fixture()
def player(monkeypatch):
    ran: list = []
    monkeypatch.setattr(music, "_status", lambda: dict(NOW))
    monkeypatch.setattr(music, "_run", lambda argv, *_: (ran.append(argv), (True, ""))[1])
    monkeypatch.setattr(music, "_spawn", lambda fn, *args: fn(*args))
    monkeypatch.setattr(music_discover, "resolve",
                        lambda artist, title: None if title == "Gone" else f"yt:https://youtu.be/{title[:11]:_<11}")
    return ran


def test_a_playlist_is_made_listed_and_edited(server, signed_in, audio_host, player):
    res, obj = call(server, "POST", "/music", {"action": "playlist", "op": "make", "title": "Main character energy",
                                               "songs": ["Lizzo - Good as Hell", {"artist": "Queen", "title": "Don't Stop Me Now"}]},
                    AUTH)
    assert res.status == 200, obj
    row = obj["playlist"]
    assert row["uri"] == "playlist:main-character-energy" and row["tracks"] == 2
    assert row["songs"][0] == {"n": 1, "artist": "Lizzo", "title": "Good as Hell", "uri": None}
    call(server, "POST", "/music", {"action": "playlist", "op": "add", "id": row["id"], "songs": ["Heroes"]}, AUTH)
    res, obj = call(server, "POST", "/music", {"action": "playlist", "op": "move", "id": row["id"], "n": 3, "to": 1}, AUTH)
    assert [s["title"] for s in obj["playlist"]["songs"]] == ["Heroes", "Good as Hell", "Don't Stop Me Now"]
    res, obj = call(server, "GET", "/music/recent?list=playlists", None, AUTH)
    assert res.status == 200 and [i["title"] for i in obj["items"]] == ["Main character energy"]
    res, obj = call(server, "POST", "/music", {"action": "playlist", "op": "remove", "id": row["id"], "n": 9}, AUTH)
    assert res.status == 400
    res, obj = call(server, "POST", "/music", {"action": "playlist", "op": "delete", "id": "Main Character  Energy"}, AUTH)
    assert res.status == 200 and mp.all_playlists() == []
    assert player == []


def test_a_playlist_plays_in_order_skipping_what_cant_be_found(server, signed_in, audio_host, player):
    p = mp.make("Mix", ["X - Gone", "A - First", "yt:https://www.youtube.com/watch?v=Gkl8blLusFc", "B - Third"])
    res, obj = call(server, "POST", "/music", {"action": "play", "uri": f"playlist:{p['id']}"}, AUTH)
    assert res.status == 200, obj
    assert obj["missed"] == ["X - Gone"] and obj["queued"] == 2
    assert player == [["play", "yt:https://youtu.be/First______"],
                      ["play", "yt:https://www.youtube.com/watch?v=Gkl8blLusFc", "--add"],
                      ["play", "yt:https://youtu.be/Third______", "--add"]]


def test_an_unknown_playlist_is_404(server, signed_in, audio_host, player):
    res, obj = call(server, "POST", "/music", {"action": "playlist", "op": "play", "id": "nope"}, AUTH)
    assert res.status == 404 and player == []


def test_a_song_is_named_or_an_address():
    assert mp.parse_song("Florence + the Machine – Dog Days Are Over") == {
        "artist": "Florence + the Machine", "title": "Dog Days Are Over"}
    assert mp.parse_song("yt:https://youtu.be/x") == {"artist": "", "title": "", "uri": "yt:https://youtu.be/x"}
    with pytest.raises(ValueError):
        mp.make("  ", ["A - B"])
