"""`POST /music {"action": "song", "id"}` (§6.9a) and a reply's `spoken.song`
(§6.2.2): the song the music bed played under a reply, played again in the
music player (David, 30 Oct 2026: "let's also add to the reply menu, a way of
playing the actual song through the music player").

Nothing reaches a player: the `media music` runner and the speech check are
faked at music.py's seams; the history rows are in the test's own state dir.
"""

from __future__ import annotations

import time

import pytest

from agent_media_server import music, transcript

from test_contract import AUTH, audio_host, call, server, signed_in, typed  # noqa: F401

FAV = {"source": "favourites", "title": "Teardrop", "artist": "Massive Attack",
       "video_id": "u7K72X4eo_s", "uri": "yt:u7K72X4eo_s"}
LIB = {"source": "library", "title": "Gymnopédie No. 1", "artist": "Erik Satie",
       "page": "https://archive.org/details/gymno", "file": "/tmp/x.mp3", "site": "archive.org",
       "uri": "https://archive.org/download/gymno/gymno.mp3"}


@pytest.fixture()
def player(monkeypatch):
    ran: list = []
    monkeypatch.setattr(music, "_run", lambda argv: (ran.append(argv), (True, ""))[1])
    monkeypatch.setattr(music, "_answer", lambda: {"now": None})
    monkeypatch.setattr(music, "_speaking", lambda: False)
    yield ran


def _row(extras: dict, sink: str = "speech") -> int:
    from agent_media_core.state.store import StateStore
    return StateStore().add_history(sink=sink, uri="/tmp/c.mp3", started_at=time.time(),
                                    text="A long reply.", extras=extras)


@pytest.mark.parametrize("song,argv", [
    (FAV, ["play", "yt:u7K72X4eo_s", "--title=Teardrop — Massive Attack"]),
    (LIB, ["play", "https://archive.org/download/gymno/gymno.mp3", "--title=Gymnopédie No. 1 — Erik Satie"]),
])
def test_a_replys_song_is_played(server, signed_in, audio_host, player, song, argv):
    rid = _row({"source_session": "s1", "bed_song": song})
    res, obj = call(server, "POST", "/music", {"action": "song", "id": rid, "session": "s1"}, headers=AUTH)
    assert res.status == 200, obj
    assert player == [argv]
    assert obj["song"] == {"source": song["source"], "title": song["title"],
                           "artist": song["artist"], "when": "now"}


@pytest.mark.parametrize("extras", [
    {"source_session": "s1"},                                         # no song
    {"source_session": "s1", "bed_song": {"source": "generated"}},     # nothing to play
    {"source_session": "s1", "bed_song": {**FAV, "uri": "/etc/passwd"}},
    {"source_session": "s1", "bed_song": {**FAV, "uri": "--help"}},
    {"source_session": "other", "bed_song": FAV},                      # another thread's
])
def test_no_song_is_404_and_plays_nothing(server, signed_in, audio_host, player, extras):
    rid = _row(extras)
    res, obj = call(server, "POST", "/music", {"action": "song", "id": rid, "session": "s1"}, headers=AUTH)
    assert res.status == 404 and obj["ok"] is False and player == []


def test_only_a_speech_row(server, signed_in, audio_host, player):
    rid = _row({"bed_song": FAV}, sink="music")
    res, _ = call(server, "POST", "/music", {"action": "song", "id": rid}, headers=AUTH)
    assert res.status == 404 and player == []


@pytest.mark.parametrize("rid", [None, "3", True, 0, -1])
def test_a_bad_id_is_400(server, signed_in, audio_host, player, rid):
    res, _ = call(server, "POST", "/music", {"action": "song", "id": rid}, headers=AUTH)
    assert res.status == 400 and player == []


def test_while_a_reply_is_read_it_waits_for_the_end(server, signed_in, audio_host, player, monkeypatch):
    """Never over the reading, never cutting it off: and a streaming bed's
    tidy-up (Mopidy stopped, its tracks taken back) cannot stop it."""
    speaking = [True]
    monkeypatch.setattr(music, "_speaking", lambda: speaking[0])
    monkeypatch.setattr(music, "_SONG_POLL_S", 0.02)
    monkeypatch.setattr(music, "_SONG_SETTLE_S", 0.0)
    rid = _row({"bed_song": FAV})
    res, obj = call(server, "POST", "/music", {"action": "song", "id": rid}, headers=AUTH)
    assert res.status == 200 and obj["song"]["when"] == "after"
    time.sleep(0.15)
    assert player == []
    speaking[0] = False
    for _ in range(100):
        if player:
            break
        time.sleep(0.02)
    assert player == [["play", "yt:u7K72X4eo_s", "--title=Teardrop — Massive Attack"]]


def test_a_failed_play_is_502(server, signed_in, audio_host, player, monkeypatch):
    monkeypatch.setattr(music, "_run", lambda argv: (False, "no player"))
    rid = _row({"bed_song": FAV})
    res, obj = call(server, "POST", "/music", {"action": "song", "id": rid}, headers=AUTH)
    assert res.status == 502 and obj["error"] == "no player"


def test_song_is_gated(server, monkeypatch, audio_host, player):
    rid = _row({"bed_song": FAV})
    res, _ = call(server, "POST", "/music", {"action": "song", "id": rid})
    assert res.status in (401, 403) and player == []


def test_spoken_carries_the_song():
    line = {"id": 7, "key": "k", "at": 1.0,
            "song": {"source": "favourites", "title": "Teardrop", "artist": "Massive Attack"}}
    assert transcript._spoken(line)["song"] == line["song"]
    assert "song" not in transcript._spoken({"id": 8, "key": "", "at": 2.0})
