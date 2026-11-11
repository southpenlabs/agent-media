"""`GET /music` and `POST /music` (§6.9a): the Media tab's now playing and controls.

Nothing reaches a player: the status read and the `media music` runner are
faked at the module's seams, and what is pinned is the HTTP answer and the
verbs the runner is handed.
"""

from __future__ import annotations

import pytest

from agent_media_server import auth_abs, music

from test_contract import AUTH, audio_host, call, keys, server, signed_in, typed  # noqa: F401

NOW = {"backend": "phone", "uri": "yt:https://www.youtube.com/watch?v=nMDHjVVj3bA", "media_id": "nMDHjVVj3bA", "path": "http://localhost:6616/nMDHjVVj3bA.mka",
       "title": "A Mix", "chapter": None, "pos_ms": 7000, "dur_ms": 3019000,
       "paused": False, "speed": 1.0, "volume": 100, "held": False}
#: NOW as answered: the artist lookup knows nothing here, so the title is the song.
SAID = {**NOW, "artist": None, "song": "A Mix", "album": None, "channel": None}


@pytest.fixture(autouse=True)
def _no_phone(monkeypatch):
    """A track's chapters are read on the phone over ssh; never here."""
    from agent_media_core.sinks import music_sasonica
    monkeypatch.setattr(music_sasonica, "chapters", lambda url: [])
    from agent_media_core import media_meta
    monkeypatch.setattr(media_meta, "known", lambda key: None)
    monkeypatch.setattr(media_meta, "fill", lambda wanted: None)
    from agent_media_core import music_chapters
    monkeypatch.setattr(music_chapters, "fill", lambda vids: None)


@pytest.fixture()
def player(monkeypatch):
    ran: list = []
    monkeypatch.setattr(music, "_status", lambda: dict(NOW))
    monkeypatch.setattr(music, "_run", lambda argv, *_: (ran.append(argv), (True, ""))[1])
    music._reset_cache(forget=True)
    yield ran
    music._reset_cache(forget=True)


def test_now_is_the_status_and_the_picker(server, signed_in, audio_host, player, monkeypatch):
    monkeypatch.delenv("MEDIA_RADIO_YOUTUBE", raising=False)
    res, obj = call(server, "GET", "/music", headers=AUTH)
    assert res.status == 200, obj
    assert keys(obj) == {"ok", "now", "art", "chapters", "where", "radio", "bed", "verdict", "rotation", "downloads", "mood"}
    assert obj["bed"] == {"suggested": False} and obj["verdict"] is None
    assert obj["downloads"] == []
    assert obj["radio"] == {"on": False, "seed": None, "next": [], "available": False}
    assert obj["art"] == "https://i.ytimg.com/vi/nMDHjVVj3bA/hqdefault.jpg"
    assert obj["now"] == SAID and obj["chapters"] == []


def test_downloads_show_their_progress(server, signed_in, audio_host, player, monkeypatch):
    """What is downloading, and how far (David, 10 Nov 2026: "a little
    progress indicator when something is downloading"): every download in
    `downloads`, and the radio's next song's on its row of `radio.next`."""
    from agent_media_core import downloads

    monkeypatch.setattr(music, "_radio", lambda: {
        "on": True, "seed": {"id": "nMDHjVVj3bA", "title": "A Mix"}, "available": True, "more": 0,
        "next": [{"id": "aaaaaaaaaaa", "title": "Next", "channel": "C", "state": "fetching"},
                 {"id": "bbbbbbbbbbb", "title": "After", "channel": "C", "state": None}]})
    with downloads.track("aaaaaaaaaaa", "radio", "Next") as radio_dl, \
            downloads.track("ccccccccccc", "book", "A Talk") as book_dl:
        radio_dl.feed("[download]  42.3% of    3.20MiB at  1.00MiB/s ETA 00:02")
        book_dl.set_phase("copying")
        _, obj = call(server, "GET", "/music", headers=AUTH)
        assert sorted(obj["downloads"], key=lambda d: d["id"]) == [
            {"id": "aaaaaaaaaaa", "purpose": "radio", "title": "Next", "pct": 42.3, "phase": "downloading"},
            {"id": "ccccccccccc", "purpose": "book", "title": "A Talk", "pct": None, "phase": "copying"}]
        assert obj["radio"]["next"][0]["pct"] == 42.3
        assert "pct" not in obj["radio"]["next"][1]
    music._reset_cache()
    _, obj = call(server, "GET", "/music", headers=AUTH)
    assert obj["downloads"] == [] and "pct" not in obj["radio"]["next"][0]


def test_the_verdict_is_remembered(server, signed_in, audio_host, player, monkeypatch):
    """♥ and 👎 stay lit when the song is come back to (David, 1 Nov 2026)."""
    from agent_media_core import radio
    from agent_media_core.state import StateStore
    StateStore().add_like("music", "nMDHjVVj3bA", title="A Mix")
    _, obj = call(server, "GET", "/music", headers=AUTH)
    assert obj["verdict"] == "like"
    music._reset_cache()
    monkeypatch.setattr(radio, "disliked", lambda vid: vid == "nMDHjVVj3bA")
    _, obj = call(server, "GET", "/music", headers=AUTH)
    assert obj["verdict"] == "dislike"


def test_a_like_is_taken_back(server, signed_in, audio_host, player):
    from agent_media_core.state import StateStore
    StateStore().add_like("music", "nMDHjVVj3bA", title="A Mix")
    res, obj = call(server, "POST", "/music", {"action": "unlike"}, headers=AUTH)
    assert res.status == 200, obj
    assert obj["verdict"] is None and StateStore().list_likes(channel="music") == []
    assert player == []  # nothing was played or skipped


def test_a_dislike_is_taken_back_on_every_station(server, signed_in, audio_host, player):
    import json
    from agent_media_core import radio
    with radio._station() as st:
        st.update(banned=["nMDHjVVj3bA"], strikes={"Chan": 1},
                  sent=[{"id": "nMDHjVVj3bA", "title": "A Mix", "channel": "Chan"}])
    radio._stations_path().write_text(json.dumps([{"sid": "1", "banned": ["nMDHjVVj3bA", "other"], "strikes": {"Chan": 2},
                                                   "sent": [{"id": "nMDHjVVj3bA", "channel": "Chan"}]}]))
    _, obj = call(server, "GET", "/music", headers=AUTH)
    assert obj["verdict"] == "dislike"
    res, obj = call(server, "POST", "/music", {"action": "undislike"}, headers=AUTH)
    assert res.status == 200, obj
    assert obj["verdict"] is None
    assert radio.read()["banned"] == [] and radio.read()["strikes"] == {}
    assert radio._kept()[0]["banned"] == ["other"] and radio._kept()[0]["strikes"] == {"Chan": 1}
    assert player == []


def test_a_like_on_a_mix_is_its_chapter(server, signed_in, audio_host, player, monkeypatch):
    from agent_media_core.state import StateStore
    StateStore().add_like("music", "nMDHjVVj3bA", title="A Mix", chapter="Track two")
    monkeypatch.setattr(music, "_status", lambda: {**NOW, "chapter": "Track one"})
    _, obj = call(server, "GET", "/music", headers=AUTH)
    assert obj["verdict"] is None
    music._reset_cache()
    monkeypatch.setattr(music, "_status", lambda: {**NOW, "chapter": "Track two"})
    _, obj = call(server, "GET", "/music", headers=AUTH)
    assert obj["verdict"] == "like"


def test_now_names_the_artist(server, signed_in, audio_host, player, monkeypatch):
    from agent_media_core import media_meta
    monkeypatch.setattr(media_meta, "known", lambda key: {"artist": "Massive Attack", "song": "Teardrop", "album": "Mezzanine", "found": True}
                        if key == "yt:nMDHjVVj3bA" else None)
    _, obj = call(server, "GET", "/music", headers=AUTH)
    assert obj["now"]["artist"] == "Massive Attack" and obj["now"]["song"] == "Teardrop"
    assert obj["now"]["album"] == "Mezzanine"
    assert keys(obj["where"]) == {"current", "next", "overridden", "options", "rooms"}
    assert obj["where"]["rooms"] == []  # no music stream on this host


@pytest.mark.parametrize("body,argv", [
    ({"action": "pause"}, ["pause"]),
    ({"action": "resume"}, ["resume"]),
    ({"action": "toggle"}, ["toggle"]),
    ({"action": "next"}, ["next"]),
    ({"action": "prev"}, ["prev", "--restart-first"]),
    ({"action": "stop"}, ["stop"]),
    ({"action": "like"}, ["like"]),
    ({"action": "seek", "to": 600}, ["seek", "0:10:00"]),
    ({"action": "seek", "to": 3725.4}, ["seek", "1:02:05"]),
    ({"action": "seek-by", "by": 30}, ["seek", "+30"]),
    ({"action": "seek-by", "by": -15}, ["seek", "-15"]),
    ({"action": "play", "uri": "yt:https://www.youtube.com/watch?v=Gkl8blLusFc"},
     ["play", "yt:https://www.youtube.com/watch?v=Gkl8blLusFc"]),
    ({"action": "add", "uri": "https://www.youtube.com/watch?v=Gkl8blLusFc"},
     ["play", "https://www.youtube.com/watch?v=Gkl8blLusFc", "--add"]),
    # A mix's chapter (music_chapters): the clip from its start.
    ({"action": "play", "uri": "yt:https://www.youtube.com/watch?v=Gkl8blLusFc", "start": 424},
     ["play", "yt:https://www.youtube.com/watch?v=Gkl8blLusFc", "--start=424"]),
    ({"action": "play", "uri": "yt:https://www.youtube.com/watch?v=Gkl8blLusFc", "start": 0},
     ["play", "yt:https://www.youtube.com/watch?v=Gkl8blLusFc"]),
])
def test_controls_are_the_cli_verbs(server, signed_in, audio_host, player, body, argv):
    res, obj = call(server, "POST", "/music", body, AUTH)
    assert res.status == 200, obj
    assert player == [argv]
    assert obj["now"] == SAID


@pytest.mark.parametrize("body", [{}, {"action": "quit"}, {"action": "seek"},
                                  {"action": "play"}, {"action": "play", "uri": "--where rooms"},
                                  {"action": "add", "uri": "/etc/passwd"},
                                  {"action": "seek", "to": "10:00"}, {"action": "seek-by", "by": True},
                                  {"action": "play", "uri": "yt:Gkl8blLusFc", "start": -1},
                                  {"action": "play", "uri": "yt:Gkl8blLusFc", "start": "424"}])
def test_a_bad_control_is_400_and_runs_nothing(server, signed_in, audio_host, player, body):
    res, obj = call(server, "POST", "/music", body, AUTH)
    assert res.status == 400 and obj["ok"] is False and isinstance(obj["error"], str)
    assert player == []


def test_a_failed_control_is_502_with_the_state(server, signed_in, audio_host, player, monkeypatch):
    monkeypatch.setattr(music, "_run", lambda argv, *_: (False, "nothing playing"))
    res, obj = call(server, "POST", "/music", {"action": "next"}, AUTH)
    assert res.status == 502 and obj["error"] == "nothing playing" and obj["now"] == SAID


@pytest.mark.parametrize("who,status", [((None, 401), 401),
                                        (({"username": "guest", "type": "user"}, 200), 403)])
def test_controls_are_gated(server, monkeypatch, audio_host, player, who, status):
    monkeypatch.setattr(auth_abs, "abs_identity", lambda bearer: who)
    res, obj = call(server, "POST", "/music", {"action": "pause"}, AUTH)
    assert res.status == status and obj["ok"] is False
    assert player == []


def test_reading_is_kept_a_moment(server, signed_in, audio_host, monkeypatch):
    reads = []
    monkeypatch.setattr(music, "_status", lambda: (reads.append(1), dict(NOW))[1])
    music._reset_cache()
    call(server, "GET", "/music", headers=AUTH)
    call(server, "GET", "/music", headers=AUTH)
    assert len(reads) == 1
    music._reset_cache()


def test_a_mix_lists_its_tracks(server, signed_in, audio_host, player, monkeypatch):
    from agent_media_core.sinks import music_sasonica
    monkeypatch.setattr(music_sasonica, "phone_path", lambda url: "/x.mka" if url.startswith("http://localhost:6616/") else None)
    monkeypatch.setattr(music_sasonica, "chapters", lambda url: [
        {"title": "Sun Salutation", "start": 0.0, "end": 264.0},
        {"title": "Trance Life", "start": 674.5, "end": 800.0}])
    _, obj = call(server, "GET", "/music", headers=AUTH)
    assert obj["chapters"] == [{"title": "Sun Salutation", "start_ms": 0},
                               {"title": "Trance Life", "start_ms": 674500}]


def test_a_failed_read_after_a_track_keeps_the_track(server, signed_in, audio_host, monkeypatch):
    reads = [dict(NOW), {"backend": "mopidy", "pos_ms": None}]
    monkeypatch.setattr(music, "_status", lambda: reads.pop(0) if len(reads) > 1 else reads[0])
    music._reset_cache(forget=True)
    _, first = call(server, "GET", "/music", headers=AUTH)
    music._reset_cache()
    _, second = call(server, "GET", "/music", headers=AUTH)
    assert first["now"] == SAID and second["now"] == SAID
    music._reset_cache(forget=True)


def test_a_stop_is_believed_at_once(server, signed_in, audio_host, monkeypatch):
    monkeypatch.setattr(music, "_run", lambda argv, *_: (True, ""))
    states = [dict(NOW)]
    monkeypatch.setattr(music, "_status", lambda: states[0])
    music._reset_cache(forget=True)
    call(server, "GET", "/music", headers=AUTH)
    states[0] = {"backend": "mopidy", "pos_ms": None}
    _, obj = call(server, "POST", "/music", {"action": "stop"}, AUTH)
    assert obj["now"]["pos_ms"] is None
    music._reset_cache(forget=True)


def test_recent_is_the_played_list(server, signed_in, audio_host, monkeypatch):
    from agent_media_core import music_recent
    items = [{"id": 3, "uri": "yt:https://www.youtube.com/watch?v=Gkl8blLusFc", "title": "As Hope",
              "at": 1790596000.0, "session": "ed0469b8", "inferred": True}]
    monkeypatch.setattr(music_recent, "recent", lambda kind="music", **_: items if kind == "music" else [])
    monkeypatch.setattr(music_recent, "_liked", lambda store, limit: items[:1])
    res, obj = call(server, "GET", "/music/recent", headers=AUTH)
    assert res.status == 200 and obj == {"ok": True, "kind": "music", "list": "recent",
                                         "lists": ["recent", "liked", "most", "stations", "albums", "sung"], "items": items}
    res, obj = call(server, "GET", "/music/recent?kind=book", headers=AUTH)
    assert res.status == 200 and obj["kind"] == "book" and obj["items"] == []
    assert obj["lists"] == ["recent", "progress", "finished", "all", "most", "added", "unstarted"]
    res, obj = call(server, "GET", "/music/recent?list=liked", headers=AUTH)
    assert res.status == 200 and obj["list"] == "liked" and obj["items"] == items[:1]
    res, obj = call(server, "GET", "/music/recent?kind=video", headers=AUTH)
    assert res.status == 400 and obj["ok"] is False
    res, obj = call(server, "GET", "/music/recent?kind=music&list=progress", headers=AUTH)
    assert res.status == 400 and obj["ok"] is False


def test_a_book_is_played_only_from_the_list(server, signed_in, audio_host, monkeypatch):
    from agent_media_core import music_recent
    books = [{"uri": "/home/u/audiobooks/Hounded.m4b", "kind": "book"}]
    monkeypatch.setattr(music_recent, "recent", lambda kind="music", **_: books if kind == "book" else [])
    monkeypatch.setattr(music_recent, "_library_books", lambda which: [])
    ran = []
    monkeypatch.setattr(music, "_run_book", lambda uri, *_: (ran.append(uri), (True, ""))[1])
    monkeypatch.setattr(music, "_answer", lambda: {"now": None})
    res, obj = call(server, "POST", "/music", {"action": "book", "uri": "/etc/passwd"}, headers=AUTH)
    assert res.status == 400 and ran == []
    res, obj = call(server, "POST", "/music", {"action": "book", "uri": "/home/u/audiobooks/Hounded.m4b"}, headers=AUTH)
    assert res.status == 200 and ran == ["/home/u/audiobooks/Hounded.m4b"]


def test_recent_searches_with_q(server, signed_in, audio_host, monkeypatch):
    """`?q=` (9 Nov 2026): the server searches, the whole history."""
    from agent_media_core import music_recent
    asked = []
    monkeypatch.setattr(music_recent, "listed", lambda kind, which, q="": asked.append((kind, which, q)) or [{"id": 1}])
    res, obj = call(server, "GET", "/music/recent?q=vani%20born", headers=AUTH)
    assert res.status == 200 and obj["q"] == "vani born" and obj["items"] == [{"id": 1}]
    res, obj = call(server, "GET", "/music/recent?kind=book&list=most&q=druid", headers=AUTH)
    assert res.status == 200 and asked == [("music", "recent", "vani born"), ("book", "most", "druid")]
    _, obj = call(server, "GET", "/music/recent", headers=AUTH)
    assert "q" not in obj and asked[-1] == ("music", "recent", "")


def test_a_channels_page_route(server, signed_in, audio_host, monkeypatch):
    from agent_media_core import music_channel
    asked = []
    page = {"name": "VaniBorn", "key": "vaniborn", "url": "https://www.youtube.com/@vaniborn",
            "youtube": "https://www.youtube.com/channel/UC1", "details": None, "played": [], "uploads": [],
            "sources": {"details": None, "uploads": None}}
    monkeypatch.setattr(music_channel, "page", lambda **kw: asked.append(kw) or page)
    res, obj = call(server, "GET", "/music/channel?name=vaniBorn", headers=AUTH)
    assert res.status == 200 and obj == {"ok": True, "channel": page}
    res, obj = call(server, "GET", "/music/channel?vid=MMMMMMMMMMM&name=VaniBorn", headers=AUTH)
    assert res.status == 200 and asked[-1] == {"name": "VaniBorn", "url": "", "vid": "MMMMMMMMMMM"}
    res, obj = call(server, "GET", "/music/channel", headers=AUTH)
    assert res.status == 400 and obj["ok"] is False
    res, obj = call(server, "GET", "/music/channel?url=https://evil.example/x", headers=AUTH)
    assert res.status == 400 and len(asked) == 2
    monkeypatch.setattr(auth_abs, "abs_identity", lambda bearer: (None, 401))
    res, obj = call(server, "GET", "/music/channel?name=x", headers=AUTH)
    assert res.status == 401


def test_now_carries_its_channel(server, signed_in, audio_host, player, monkeypatch):
    from agent_media_core import music_channel
    monkeypatch.setattr(music_channel, "row", lambda vid: {"name": "vaniBorn", "key": "vaniborn"} if vid == "nMDHjVVj3bA" else None)
    _, obj = call(server, "GET", "/music", headers=AUTH)
    assert obj["now"]["channel"] == {"name": "vaniBorn", "key": "vaniborn"}


def test_recent_is_gated(server, monkeypatch, audio_host):
    monkeypatch.setattr(auth_abs, "abs_identity", lambda bearer: (None, 401))
    res, obj = call(server, "GET", "/music/recent", headers=AUTH)
    assert res.status == 401 and obj["ok"] is False


@pytest.fixture()
def station(monkeypatch, tmp_path):
    """The radio with its Mix and player faked (agent_media_core.radio)."""
    from agent_media_core import cli, radio
    from agent_media_server import radio as loop

    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    monkeypatch.setenv("MEDIA_RADIO_YOUTUBE", "1")
    started: list = []

    def start(uri, where, playing):
        started.append((uri, where, playing))
        with radio._station() as st:
            st.update(on=True, where=where, seed={"id": "nMDHjVVj3bA", "title": "A Mix"},
                      queue=[{"id": "Gkl8blLusFc", "title": "Next", "channel": "Them"}])
        return radio.snapshot()

    monkeypatch.setattr(radio, "start", start)
    monkeypatch.setattr(radio, "_skip", lambda where: None)
    monkeypatch.setattr(cli, "_resolve_music_where", lambda w: "sasonica")
    monkeypatch.setattr(loop, "wake", lambda: None)
    return started


def test_radio_starts_from_what_is_playing(server, signed_in, audio_host, player, station):
    res, obj = call(server, "POST", "/music", {"action": "radio"}, AUTH)
    assert res.status == 200, obj
    assert station == [(NOW["uri"], "sasonica", True)] and player == []
    assert obj["radio"]["on"] is True
    assert obj["radio"]["next"] == [{"id": "Gkl8blLusFc", "title": "Next", "channel": "Them", "state": None}]


def test_radio_from_a_uri_plays_it_first(server, signed_in, audio_host, player, station):
    uri = "yt:https://www.youtube.com/watch?v=Gkl8blLusFc"
    res, obj = call(server, "POST", "/music", {"action": "radio", "uri": uri}, AUTH)
    assert res.status == 200, obj
    assert player == [["play", uri]] and station == [(uri, "sasonica", True)]


def test_radio_off_and_dislike(server, signed_in, audio_host, player, station):
    call(server, "POST", "/music", {"action": "radio"}, AUTH)
    res, obj = call(server, "POST", "/music", {"action": "dislike"}, AUTH)
    assert res.status == 200 and obj["radio"]["on"] is True
    res, obj = call(server, "POST", "/music", {"action": "radio", "off": True}, AUTH)
    assert res.status == 200 and obj["radio"]["on"] is False and player == []


def test_radio_plays_a_song_from_up_next(server, signed_in, audio_host, player, station, monkeypatch):
    from agent_media_core import radio

    tapped: list = []
    monkeypatch.setattr(radio, "play", lambda vid: (tapped.append(vid), radio.snapshot())[1] if radio.is_on() else None)
    res, obj = call(server, "POST", "/music", {"action": "radio", "play": "Gkl8blLusFc"}, AUTH)
    assert res.status == 409 and tapped == []
    call(server, "POST", "/music", {"action": "radio"}, AUTH)
    res, obj = call(server, "POST", "/music", {"action": "radio", "play": "Gkl8blLusFc"}, AUTH)
    assert res.status == 200 and tapped == ["Gkl8blLusFc"] and obj["radio"]["on"] is True
    res, obj = call(server, "POST", "/music", {"action": "radio", "play": "; rm -rf"}, AUTH)
    assert res.status == 400 and tapped == ["Gkl8blLusFc"]


def test_dislike_with_no_station_is_a_skip(server, signed_in, audio_host, player, station):
    res, obj = call(server, "POST", "/music", {"action": "dislike"}, AUTH)
    assert res.status == 200 and player == [["next"]]


def test_radio_dj_starts_the_djs_station(server, signed_in, audio_host, player, station, monkeypatch):
    from agent_media_core import radio

    monkeypatch.setattr(radio, "start_dj", lambda where: station.append(("dj", where)))
    res, obj = call(server, "POST", "/music", {"action": "radio", "dj": True}, AUTH)
    assert res.status == 200, obj
    assert station == [("dj", "sasonica")] and player == []


def test_radio_is_409_on_a_server_that_has_not_turned_it_on(server, signed_in, audio_host, player, station, monkeypatch):
    monkeypatch.delenv("MEDIA_RADIO_YOUTUBE")
    for body in ({"action": "radio"}, {"action": "radio", "dj": True},
                 {"action": "radio", "uri": "yt:https://www.youtube.com/watch?v=Gkl8blLusFc"}):
        res, obj = call(server, "POST", "/music", body, AUTH)
        assert res.status == 409 and "MEDIA_RADIO_YOUTUBE" in obj["error"], obj
        assert obj["radio"]["available"] is False
    assert station == [] and player == []
    res, obj = call(server, "POST", "/music", {"action": "radio", "off": True}, AUTH)
    assert res.status == 200


# ---- radio from a song sung out (31 Oct 2026) ------------------------------

@pytest.fixture()
def youtube(monkeypatch, tmp_path):
    """The real radio.start with its YouTube and the app's player faked at
    radio_io: the Mix listed here, the player a stub that already has the
    song (as the phone labels it, `&v=<id>` on the clip's URL)."""
    from agent_media_core import radio, radio_io
    from agent_media_core import music_bed_favourites as fav
    from agent_media_server import radio as loop

    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    monkeypatch.setenv("MEDIA_RADIO_YOUTUBE", "1")
    asked: list = []
    monkeypatch.setattr(radio_io, "youtube_mix", lambda vid: (asked.append(vid), [
        {"id": vid, "title": "Angel", "channel": "Massive Attack"},
        {"id": "Gkl8blLusFc", "title": "Teardrop", "channel": "Massive Attack"}])[1])

    class App:
        def __init__(self, where):
            self.where = where

        def props(self):
            return {"path": "https://red5/speech/clips/music-bed-vocal-k1.mp3?sig=ab&v=66A_3uwuZ_I",
                    "idle-active": False, "playlist-pos": 0, "playlist-count": 1}

        def clear(self):
            asked.append(("clear", self.where))

    monkeypatch.setattr(radio_io, "player", lambda where: App(where))
    monkeypatch.setattr(loop, "wake", lambda: None)
    data = fav.load()
    data["tracks"] = [{"key": "k1", "file": "f.mp3", "title": "Angel", "artist": "Massive Attack",
                       "original_id": "66A_3uwuZ_I"},
                      {"key": "k2", "file": "g.mp3", "title": "Home", "artist": "Nobody",
                       "original_id": ""}]
    fav.save(data)
    return asked


def test_radio_from_a_sung_out_song(server, signed_in, audio_host, player, youtube):
    res, obj = call(server, "POST", "/music/radio", {"from_song": "k1"}, AUTH)
    assert res.status == 200, obj
    # Seeded with the original's video, on the app's player, behind the song.
    assert youtube == ["66A_3uwuZ_I", ("clear", "sasonica")]
    assert obj["radio"]["on"] is True and obj["radio"]["seed"]["id"] == "66A_3uwuZ_I"
    assert [s["id"] for s in obj["radio"]["next"]] == ["Gkl8blLusFc"]
    assert player == []                       # nothing played from here: it is on already
    # The same through POST /music.
    res, obj = call(server, "POST", "/music", {"action": "radio", "from_song": "k1"}, AUTH)
    assert res.status == 200, obj
    # Taken up: in the music history once, and in Sung out (music_sung).
    res, obj = call(server, "GET", "/music/recent", None, AUTH)
    assert [i["uri"][-11:] for i in obj["items"]] == ["66A_3uwuZ_I"]
    res, obj = call(server, "GET", "/music/recent?list=sung", None, AUTH)
    assert [i["engaged"] for i in obj["items"]] == [["radio"]]


def test_radio_from_a_song_without_an_original(server, signed_in, audio_host, player, youtube):
    res, obj = call(server, "POST", "/music/radio", {"from_song": "k2"}, AUTH)
    assert res.status == 400 and obj["error"] == "this song has no YouTube original"
    res, obj = call(server, "POST", "/music/radio", {"from_song": "nope"}, AUTH)
    assert res.status == 400
    # A key the server no longer has, with the id the phone was told.
    res, obj = call(server, "POST", "/music/radio",
                    {"from_song": "gone", "video_id": "Gkl8blLusFc"}, AUTH)
    assert res.status == 200 and youtube[-2] == "Gkl8blLusFc"


# ---- the house speakers one by one, and moving what plays (20 Oct 2026) ----

ROOMS = [{"id": "hpo-music", "label": "hpo", "connected": True, "on": True, "volume": 40}]


@pytest.fixture()
def rooms(monkeypatch):
    from agent_media_core import music_rooms

    sets: list = []
    monkeypatch.setattr(music_rooms, "rooms", lambda timeout=2.0: [dict(r) for r in ROOMS])

    def set_room(cid, on=None, volume=None, timeout=2.0):
        if cid != "hpo-music":
            raise ValueError("no room")
        sets.append((cid, on, volume))
        return dict(ROOMS[0])

    monkeypatch.setattr(music_rooms, "set_room", set_room)
    music._ROOMS[:] = [0.0, None]
    yield sets
    music._ROOMS[:] = [0.0, None]


def test_where_lists_the_rooms(server, signed_in, audio_host, player, rooms):
    res, obj = call(server, "GET", "/music", headers=AUTH)
    assert res.status == 200 and obj["where"]["rooms"] == ROOMS


def test_unreachable_rooms_are_null(server, signed_in, audio_host, player, monkeypatch):
    from agent_media_core import music_rooms, snapcast

    def down(timeout=2.0):
        raise snapcast.SnapcastError("connect: refused")

    monkeypatch.setattr(music_rooms, "rooms", down)
    music._ROOMS[:] = [0.0, None]
    res, obj = call(server, "GET", "/music", headers=AUTH)
    assert res.status == 200 and obj["where"]["rooms"] is None


@pytest.mark.parametrize("body,want", [
    ({"action": "room", "id": "hpo-music", "on": False}, ("hpo-music", False, None)),
    ({"action": "room", "id": "hpo-music", "volume": 62.4}, ("hpo-music", None, 62)),
    ({"action": "room", "id": "hpo-music", "on": True, "volume": 0}, ("hpo-music", True, 0)),
])
def test_a_room_is_set(server, signed_in, audio_host, player, rooms, body, want):
    res, obj = call(server, "POST", "/music", body, AUTH)
    assert res.status == 200, obj
    assert rooms == [want] and player == []


@pytest.mark.parametrize("body", [
    {"action": "room"}, {"action": "room", "id": "hpo-music"},
    {"action": "room", "id": "hpo-music", "on": "yes"},
    {"action": "room", "id": "hpo-music", "volume": 101},
    {"action": "room", "id": "hpo-music", "volume": True},
    {"action": "room", "id": "p8a-music", "on": True},
])
def test_a_bad_room_is_400(server, signed_in, audio_host, player, rooms, body):
    res, obj = call(server, "POST", "/music", body, AUTH)
    assert res.status == 400 and obj["ok"] is False
    assert rooms == []


@pytest.fixture()
def mover(monkeypatch):
    """The move's thread run inline, its player reads faked."""
    from agent_media_core import cli, radio
    from agent_media_core.sinks import music as sink_music

    monkeypatch.setattr(music, "_spawn", lambda fn, *args: fn(*args))
    monkeypatch.setattr(radio, "read", lambda: {"on": False})
    stopped: list = []
    monkeypatch.setattr(sink_music.SinkMusic, "stop", lambda self, *a: stopped.append("mopidy"))
    there = {"backend": "mopidy", "media_id": "nMDHjVVj3bA", "pos_ms": 0}
    monkeypatch.setattr(cli, "_music_status_json", lambda m, patient=False, where="": dict(there))
    with music._LOCK:
        music._MOVE.clear()
    yield stopped
    with music._LOCK:
        music._MOVE.clear()


def test_a_move_plays_it_there_at_the_same_spot(server, signed_in, audio_host, player, mover, monkeypatch):
    from agent_media_core import cli

    monkeypatch.setattr(cli, "_resolve_music_where", lambda w: "rooms")
    res, obj = call(server, "POST", "/music", {"action": "move"}, AUTH)
    assert res.status == 200, obj
    assert player == [["pause"], ["play", NOW["uri"], "--where", "rooms"], ["stop"], ["seek", "0:00:07"]]
    assert music._move_state() == {"to": "rooms", "state": "done", "error": None}


def test_a_move_to_where_it_is_does_nothing(server, signed_in, audio_host, player, mover, monkeypatch):
    from agent_media_core import cli

    monkeypatch.setattr(cli, "_resolve_music_where", lambda w: "sasonica")
    res, obj = call(server, "POST", "/music", {"action": "move"}, AUTH)
    assert res.status == 200 and player == [] and "move" not in obj


def test_the_radio_moves_as_a_station(server, signed_in, audio_host, player, mover, monkeypatch):
    from agent_media_core import cli, radio

    from agent_media_server import radio as loop

    moved: list = []
    monkeypatch.setattr(cli, "_resolve_music_where", lambda w: "rooms")
    monkeypatch.setattr(radio, "read", lambda: {"on": True, "where": "sasonica"})
    monkeypatch.setattr(radio, "move", lambda to, pos_ms, paused: moved.append((to, pos_ms, paused)))
    monkeypatch.setattr(loop, "wake", lambda: None)
    res, obj = call(server, "POST", "/music", {"action": "move"}, AUTH)
    assert res.status == 200, obj
    # The station's own player is moved; a play would have ended it.
    assert moved == [("rooms", 7000, False)] and player == []
    assert music._move_state() == {"to": "rooms", "state": "done", "error": None}


def test_the_radio_moves_only_to_a_radio_player(server, signed_in, audio_host, player, mover, monkeypatch):
    from agent_media_core import cli, radio

    monkeypatch.setattr(cli, "_resolve_music_where", lambda w: "rooms")
    monkeypatch.setattr(radio, "read", lambda: {"on": True, "where": "handoff"})
    res, obj = call(server, "POST", "/music", {"action": "move"}, AUTH)
    assert res.status == 409 and player == []


def test_a_failed_move_goes_back(server, signed_in, audio_host, player, mover, monkeypatch):
    from agent_media_core import cli

    monkeypatch.setattr(cli, "_resolve_music_where", lambda w: "rooms")
    ran: list = []
    monkeypatch.setattr(music, "_run", lambda argv, *_: (ran.append(argv), (argv[0] != "play", "no"))[1])
    res, obj = call(server, "POST", "/music", {"action": "move"}, AUTH)
    assert res.status == 200
    assert ran == [["pause"], ["play", NOW["uri"], "--where", "rooms"], ["resume"]]
    assert music._move_state()["state"] == "failed"


def test_bed_suggests_what_is_playing(server, signed_in, audio_host, player, monkeypatch):
    """🎼 on the player (David, 1 Nov 2026: "an easy way to be able to
    suggest tracks for backing music")."""
    from agent_media_core import music_bed_favourites as fav
    mine: dict = {}

    def suggest(uri):
        mine["k"] = {"id": fav._vid(uri)}
        return {"key": "k", "title": "Teardrop", "artist": "Massive Attack", "id": "x", "ready": True}

    def unsuggest(uri):
        mine.clear()
        return {"key": "k", "title": "Teardrop", "artist": "Massive Attack"}
    monkeypatch.setattr(fav, "suggest", suggest)
    monkeypatch.setattr(fav, "unsuggest", unsuggest)
    monkeypatch.setattr(fav, "suggested", lambda: dict(mine))
    res, obj = call(server, "POST", "/music", {"action": "bed"}, AUTH)
    assert res.status == 200, obj
    assert obj["suggested"]["title"] == "Teardrop" and obj["bed"] == {"suggested": True}
    assert mine == {"k": {"id": "nMDHjVVj3bA"}} and player == []
    res, obj = call(server, "POST", "/music", {"action": "bed", "off": True}, AUTH)
    assert res.status == 200 and obj["bed"] == {"suggested": False}


def test_bed_refuses_what_is_not_youtube(server, signed_in, audio_host, player):
    res, obj = call(server, "POST", "/music", {"action": "bed", "uri": "file:///etc/passwd"}, AUTH)
    assert res.status == 400, obj
    res, obj = call(server, "POST", "/music", {"action": "bed", "uri": 5}, AUTH)
    assert res.status == 400, obj


def test_back_to_a_station(server, signed_in, audio_host, player, monkeypatch):
    """Back to radio, and a row of Show → Stations (radio.resume; 1 Nov 2026)."""
    from agent_media_core import radio
    from agent_media_server import radio as loop
    asked = []
    monkeypatch.setattr(radio, "resume", lambda sid=None, where=None: asked.append(sid) or {})
    monkeypatch.setattr(loop, "wake", lambda: None)
    res, _ = call(server, "POST", "/music", {"action": "radio", "resume": True}, AUTH)
    assert res.status == 200
    res, _ = call(server, "POST", "/music", {"action": "play", "uri": "radio:1730419200000"}, AUTH)
    assert res.status == 200 and asked == [None, "1730419200000"]
    res, _ = call(server, "POST", "/music", {"action": "radio", "resume": "../x"}, AUTH)
    assert res.status == 400

    def none(sid=None, where=None):
        raise ValueError("no such station to go back to")
    monkeypatch.setattr(radio, "resume", none)
    res, obj = call(server, "POST", "/music", {"action": "radio", "resume": True}, AUTH)
    assert res.status == 409 and "no such station" in obj["error"]


def test_the_listening_mood_is_read_and_set(server, signed_in, audio_host, player, monkeypatch):
    """The listening mood (David, 11 Nov 2026): in every answer, set by
    `{"action": "mood"}`, 400 for a mood that is not one of the six."""
    from agent_media_core import music_mood

    monkeypatch.delenv("MEDIA_MUSIC_MOOD", raising=False)
    told: list = []
    monkeypatch.setattr(music_mood, "_follow", lambda mood, playing: told.append((mood, playing())))
    _, obj = call(server, "GET", "/music", headers=AUTH)
    assert obj["mood"]["id"] == "auto"
    assert [m["id"] for m in obj["mood"]["moods"]] == ["auto", "chill", "focus", "upbeat", "sleep", "party"]
    assert all(keys(m) == {"id", "label", "blurb"} for m in obj["mood"]["moods"])
    res, obj = call(server, "POST", "/music", {"action": "mood", "mood": "chill"}, headers=AUTH)
    assert res.status == 200, obj
    assert obj["mood"]["id"] == "chill" and music_mood.current() == "chill"
    assert told == [("chill", True)] and player == []
    res, obj = call(server, "POST", "/music", {"action": "mood", "mood": "gloomy"}, headers=AUTH)
    assert res.status == 400 and "mood must be one of" in obj["error"]
    assert music_mood.current() == "chill"


# The song card (David, 11 Nov 2026: "Can we add the like, dislike, sing it
# out and those other type of buttons to the song card?"): a song's ♥, 👎
# and 🎼 by its uri, playing or not; its rows say which are on.
OTHER = "yt:https://www.youtube.com/watch?v=Gkl8blLusFc"


def test_a_song_not_playing_is_liked_and_unliked_by_its_uri(server, signed_in, audio_host, player):
    from agent_media_core.state import StateStore
    res, obj = call(server, "POST", "/music", {"action": "like", "uri": OTHER, "title": "As Hope"}, AUTH)
    assert res.status == 200, obj
    assert obj["voted"] == {"uri": OTHER, "liked": True, "disliked": False, "bed_suggested": False, "rotation": 0, "moods": None}
    likes = StateStore().list_likes(channel="music")
    assert [(r["media_id"], r["uri"], r["title"]) for r in likes] == [("Gkl8blLusFc", OTHER, "As Hope")]
    # Twice is still one like; what is playing keeps its own verdict.
    call(server, "POST", "/music", {"action": "like", "uri": OTHER}, AUTH)
    assert len(StateStore().list_likes(channel="music")) == 1 and obj["verdict"] is None
    res, obj = call(server, "POST", "/music", {"action": "unlike", "uri": OTHER}, AUTH)
    assert res.status == 200 and obj["voted"]["liked"] is False
    assert StateStore().list_likes(channel="music") == []
    assert player == []  # nothing was played or skipped


def test_a_song_disliked_by_its_uri_is_off_every_station_and_the_backing_music(server, signed_in, audio_host, player, monkeypatch):
    """👎 on a song not playing: kept off every station (one there is and
    any to come) and never a backing-music favourite, so never sung out; its
    like and its suggestion go. Nothing is skipped."""
    from agent_media_core import music_bed_favourites as fav
    from agent_media_core import radio
    from agent_media_core.state import StateStore
    StateStore().add_like("music", "Gkl8blLusFc", uri=OTHER, title="As Hope")
    fav._write_json(fav._suggested_path(), {"k": {"id": "Gkl8blLusFc", "artist": "Chris Cornell", "title": "As Hope", "dur": 200, "at": 1}})
    with radio._station() as st:
        st.update(on=True, queue=[{"id": "Gkl8blLusFc", "title": "As Hope", "channel": "C"},
                                  {"id": "aaaaaaaaaaa", "title": "Next", "channel": "C"}])
    res, obj = call(server, "POST", "/music", {"action": "dislike", "uri": OTHER}, AUTH)
    assert res.status == 200, obj
    assert obj["voted"] == {"uri": OTHER, "liked": False, "disliked": True, "bed_suggested": False, "rotation": 0, "moods": None}
    assert player == []
    assert [s["id"] for s in radio.read()["queue"]] == ["aaaaaaaaaaa"]
    assert radio.disliked("Gkl8blLusFc") and fav.banned("Gkl8blLusFc") and not fav.suggested()
    assert StateStore().list_likes(channel="music") == []
    # A station to come never takes it from a Mix.
    with radio._station() as st:
        assert radio._merge(st, [{"id": "Gkl8blLusFc", "title": "As Hope (Live)", "channel": "D"}]) == 0
    res, obj = call(server, "POST", "/music", {"action": "undislike", "uri": OTHER}, AUTH)
    assert res.status == 200 and obj["voted"]["disliked"] is False
    assert not radio.disliked("Gkl8blLusFc") and not fav.banned("Gkl8blLusFc")
    # A like takes a 👎 back too: one verdict a song.
    call(server, "POST", "/music", {"action": "dislike", "uri": OTHER}, AUTH)
    res, obj = call(server, "POST", "/music", {"action": "like", "uri": OTHER}, AUTH)
    assert obj["voted"] == {"uri": OTHER, "liked": True, "disliked": False, "bed_suggested": False, "rotation": 0, "moods": None}


def test_a_disliked_favourite_goes_with_its_files(server, signed_in, audio_host, player):
    from agent_media_core import music_bed_favourites as fav
    data = fav.load()
    data["favourites"] = [{"key": "cornell-as-hope", "artist": "Chris Cornell", "title": "As Hope", "id": "Gkl8blLusFc", "dur": 200, "score": 5}]
    fav.save(data)
    res, obj = call(server, "POST", "/music", {"action": "dislike", "uri": OTHER}, AUTH)
    assert res.status == 200, obj
    assert fav.load()["favourites"] == [] and "cornell-as-hope" in fav.excluded()
    call(server, "POST", "/music", {"action": "undislike", "uri": OTHER}, AUTH)
    assert fav.excluded() == {}


def test_a_vote_by_uri_refuses_what_it_cannot_name(server, signed_in, audio_host, player):
    for body in ({"action": "like", "uri": "file:///etc/passwd"}, {"action": "like", "uri": 5},
                 {"action": "unlike", "uri": "--help"},
                 {"action": "dislike", "uri": "https://example.com/a.mp3"}):
        res, obj = call(server, "POST", "/music", body, AUTH)
        assert res.status == 400, (body, obj)
    # A web address that is not YouTube can be liked, not disliked.
    res, obj = call(server, "POST", "/music", {"action": "like", "uri": "https://example.com/a.mp3"}, AUTH)
    assert res.status == 200 and obj["voted"]["liked"] is True
    assert player == []


def test_recent_rows_say_which_buttons_are_on(server, signed_in, audio_host, monkeypatch):
    from agent_media_core import music_bed_favourites as fav
    from agent_media_core import music_recent, radio
    from agent_media_core.state import StateStore
    items = [{"id": 3, "uri": OTHER, "title": "As Hope"},
             {"id": 2, "uri": "yt:https://www.youtube.com/watch?v=gjuS3CS_9Bw", "title": "Spa"},
             {"id": 1, "uri": "https://example.com/a.mp3", "title": "A file"},
             {"id": -9, "uri": "radio:1730419200000", "title": "Radio: X"}]
    monkeypatch.setattr(music_recent, "recent", lambda kind="music", **_: [dict(i) for i in items])
    StateStore().add_like("music", "Gkl8blLusFc", uri=OTHER, title="As Hope")
    fav._write_json(fav._suggested_path(), {"k": {"id": "gjuS3CS_9Bw", "artist": "A", "title": "Spa", "dur": 200, "at": 1}})
    radio.dislike_song("gjuS3CS_9Bw", "Spa")
    _, obj = call(server, "GET", "/music/recent", headers=AUTH)
    rows = {r["id"]: r for r in obj["items"]}
    assert (rows[3]["liked"], rows[3]["disliked"], rows[3]["bed_suggested"]) == (True, False, False)
    assert (rows[2]["liked"], rows[2]["disliked"], rows[2]["bed_suggested"]) == (False, True, True)
    assert rows[1]["liked"] is False and "disliked" not in rows[1] and "bed_suggested" not in rows[1]
    assert "liked" not in rows[-9]


def test_bed_by_uri_answers_the_songs_buttons(server, signed_in, audio_host, player, monkeypatch):
    from agent_media_core import music_bed_favourites as fav
    mine: dict = {}

    def suggest(uri):
        mine["k"] = {"id": fav._vid(uri)}
        return {"key": "k", "title": "As Hope", "artist": "Chris Cornell", "id": "Gkl8blLusFc", "ready": False}
    monkeypatch.setattr(fav, "suggest", suggest)
    monkeypatch.setattr(fav, "suggested", lambda: dict(mine))
    monkeypatch.setattr(fav, "chosen", lambda: False)
    res, obj = call(server, "POST", "/music", {"action": "bed", "uri": OTHER}, AUTH)
    assert res.status == 200, obj
    assert obj["voted"] == {"uri": OTHER, "liked": False, "disliked": False, "bed_suggested": True, "rotation": 0, "moods": None}
    assert obj["bed"] == {"suggested": False}  # what is playing is another song


def test_a_song_disliked_from_its_card_shows_on_the_player(server, signed_in, audio_host, player):
    res, _ = call(server, "POST", "/music", {"action": "dislike", "uri": NOW["uri"]}, AUTH)
    assert res.status == 200
    music._reset_cache()
    _, obj = call(server, "GET", "/music", headers=AUTH)
    assert obj["verdict"] == "dislike"
    res, obj = call(server, "POST", "/music", {"action": "undislike"}, AUTH)
    assert res.status == 200 and obj["verdict"] is None
    assert player == []


def test_a_songs_rotation_is_turned_up_and_down(server, signed_in, audio_host, player):
    """More often / Less often (David, 11 Nov 2026: "an option to increase
    or decrease a song's rotation"): a level -3 to 3, on its rows and, for
    what is playing, in every answer."""
    from agent_media_core import music_rotation
    _, obj = call(server, "GET", "/music", headers=AUTH)
    assert obj["rotation"] == 0
    res, obj = call(server, "POST", "/music", {"action": "rotation", "uri": OTHER, "step": 1}, AUTH)
    assert res.status == 200, obj
    assert obj["voted"]["rotation"] == 1 and obj["rotation"] == 0
    for _ in range(5):
        call(server, "POST", "/music", {"action": "rotation", "uri": OTHER, "step": 1}, AUTH)
    assert music_rotation.level("Gkl8blLusFc") == 3
    res, obj = call(server, "POST", "/music", {"action": "rotation", "step": -1}, AUTH)
    assert res.status == 200 and obj["rotation"] == -1
    res, obj = call(server, "POST", "/music", {"action": "rotation", "uri": OTHER, "step": -1}, AUTH)
    assert obj["voted"]["rotation"] == 2
    for body in ({"action": "rotation", "uri": OTHER, "step": 2}, {"action": "rotation", "uri": OTHER, "step": True},
                 {"action": "rotation", "uri": "https://example.com/a.mp3", "step": 1},
                 {"action": "rotation", "uri": "file:///x", "step": 1}):
        res, obj = call(server, "POST", "/music", body, AUTH)
        assert res.status == 400, (body, obj)
    assert player == []


def test_the_pickers_honour_a_songs_rotation(server, signed_in, monkeypatch):
    """The backing music's favourite (and so Sing it out's song) and a
    station's refill weigh a song by its rotation."""
    import random
    from agent_media_core import music_bed_favourites as fav
    from agent_media_core import music_rotation, radio, radio_dj
    for _ in range(3):
        music_rotation.step("aaaaaaaaaaa", 1, "Up - Song")
        music_rotation.step("bbbbbbbbbbb", -1, "Down - Song")
    have = [{"key": "up", "id": "aaaaaaaaaaa"}, {"key": "down", "original_id": "bbbbbbbbbbb", "id": "ccccccccccc"}]
    monkeypatch.setattr(fav, "playable", lambda style=None, data=None: have)
    monkeypatch.setattr(fav, "_file", lambda e: fav._dir() / f"{e['key']}.mp3")
    monkeypatch.setattr(fav, "_mark_played", lambda key: None)
    seen = []
    monkeypatch.setattr(random, "choices", lambda pop, weights, k: (seen.append(weights), [pop[0]])[1])
    assert fav.track_for() is not None
    up, down = seen[0]
    assert up / down == pytest.approx(music_rotation.FACTOR ** 6)
    # A station's refill: turned up first, turned down left out that often.
    monkeypatch.setattr(radio.random, "random", lambda: 0.5)
    with radio._station() as st:
        radio._merge(st, [{"id": "ddddddddddd", "title": "Plain"}, {"id": "bbbbbbbbbbb", "title": "Down"},
                          {"id": "aaaaaaaaaaa", "title": "Up"}])
        assert [s["id"] for s in st["queue"]] == ["aaaaaaaaaaa", "ddddddddddd"]
    monkeypatch.setattr(radio_dj, "moment", lambda: {"local_time": "Wednesday 10:00"})  # no weather asked
    brief = radio_dj.brief(radio._blank())
    assert "Up - Song" in brief and "Down - Song" in brief


def test_a_favourites_moods_are_on_its_row(server, signed_in, audio_host, monkeypatch):
    """The moods a song plays under (David, 11 Nov 2026): a backing-music
    favourite's, by its measured tempo and energy against each mood's."""
    from agent_media_core import music_bed_favourites as fav
    from agent_media_core import music_mood, music_recent
    data = fav.load()
    data["tracks"] = [{"key": "slow", "id": "inst0000001", "original_id": "Gkl8blLusFc", "file": "x.mp3",
                       "features": {"tempo": 0.2, "energy": 0.25}},
                      {"key": "loud", "id": "gjuS3CS_9Bw", "file": "y.mp3", "features": {"tempo": 0.8, "energy": 0.8}}]
    fav.save(data)
    items = [{"id": 3, "uri": OTHER}, {"id": 2, "uri": "yt:https://www.youtube.com/watch?v=gjuS3CS_9Bw"},
             {"id": 1, "uri": "yt:https://www.youtube.com/watch?v=aaaaaaaaaaa"}]
    monkeypatch.setattr(music_recent, "recent", lambda kind="music", **_: [dict(i) for i in items])
    _, obj = call(server, "GET", "/music/recent", headers=AUTH)
    rows = {r["id"]: r for r in obj["items"]}
    assert rows[3]["moods"] == ["chill", "sleep"]
    assert rows[2]["moods"] == ["upbeat", "party"]
    assert "moods" not in rows[1]
    assert music_mood.favoured({"tempo": 0.5}) is None


# Other versions of a song (David, 11 Nov 2026: "Can we also have a button
# for alternative versions of a song?"): the host's YouTube search, faked.
def _mopidy(monkeypatch, found):
    from agent_media_core import music_bed_favourites as fav
    asked = []
    monkeypatch.setattr(fav, "_may_search", lambda: True)
    monkeypatch.setattr(fav, "search", lambda q: (asked.append(q), found)[1])
    return asked


def test_versions_are_other_recordings_labelled_by_kind(server, signed_in, audio_host, monkeypatch):
    from agent_media_core import media_meta
    monkeypatch.setattr(media_meta, "known", lambda key: {"artist": "Chris Cornell", "song": "As Hope And Promise Fade", "album": None, "genre": None})
    found = [
        {"id": "Gkl8blLusFc", "title": "As Hope And Promise Fade", "artist": "Chris Cornell - Topic", "dur": 200},
        {"id": "live0000001", "title": "Chris Cornell - As Hope And Promise Fade (Live at the Fillmore)", "artist": "ChrisCornellVEVO", "dur": 260},
        {"id": "acou0000001", "title": "As Hope and Promise Fade - Acoustic", "artist": "Chris Cornell", "dur": 190},
        {"id": "cove0000001", "title": "As Hope And Promise Fade", "artist": "Some Singer", "dur": 210},
        {"id": "remx0000001", "title": "As Hope And Promise Fade (Night Remix)", "artist": "DJ X", "dur": None},
        {"id": "lyrc0000001", "title": "Chris Cornell - As Hope And Promise Fade (Lyrics)", "artist": "Lyric Land", "dur": 200},
        {"id": "live0000001", "title": "Chris Cornell - As Hope And Promise Fade (Live at the Fillmore)", "artist": "ChrisCornellVEVO", "dur": 260},
        {"id": "othr0000001", "title": "Black Hole Sun", "artist": "Soundgarden", "dur": 300},
    ]
    asked = _mopidy(monkeypatch, found)
    res, obj = call(server, "GET", "/music/versions?uri=" + OTHER, headers=AUTH)
    assert res.status == 200, obj
    assert obj["source"] == "youtube-search" and obj["query"] == "Chris Cornell As Hope And Promise Fade"
    got = [(v["id"], v["kind"]) for v in obj["versions"]]
    assert got == [("lyrc0000001", "original"), ("live0000001", "live"), ("acou0000001", "acoustic"),
                   ("remx0000001", "remix"), ("cove0000001", "cover")]
    live = obj["versions"][1]
    assert live == {"uri": "yt:https://www.youtube.com/watch?v=live0000001", "id": "live0000001",
                    "title": "Chris Cornell - As Hope And Promise Fade (Live at the Fillmore)",
                    "channel": "ChrisCornellVEVO", "duration_s": 260, "kind": "live"}
    assert obj["versions"][3]["duration_s"] is None
    # Kept: asked again, the search is not.
    n = len(asked)
    call(server, "GET", "/music/versions?uri=" + OTHER, headers=AUTH)
    assert len(asked) == n


def test_versions_without_a_search_or_a_name(server, signed_in, audio_host, monkeypatch):
    from agent_media_core import music_bed_favourites as fav, youtube_addon
    monkeypatch.setattr(fav, "_may_search", lambda: False)
    monkeypatch.setattr(youtube_addon, "get", lambda name: None)
    res, obj = call(server, "GET", "/music/versions?uri=" + OTHER + "&title=Teardrop%20%E2%80%94%20Massive%20Attack", headers=AUTH)
    assert res.status == 200 and obj["versions"] == [] and obj["source"] is None
    res, obj = call(server, "GET", "/music/versions?uri=file:///x", headers=AUTH)
    assert res.status == 400


def test_versions_are_gated(server, monkeypatch, audio_host):
    monkeypatch.setattr(auth_abs, "abs_identity", lambda bearer: (None, 401))
    res, obj = call(server, "GET", "/music/versions?uri=" + OTHER, headers=AUTH)
    assert res.status == 401 and obj["ok"] is False


def test_versions_from_the_djs_search_one_line_a_kind(server, signed_in, audio_host, monkeypatch):
    from agent_media_core import music_bed_favourites as fav, radio_dj, youtube_addon
    monkeypatch.setattr(fav, "_may_search", lambda: False)
    monkeypatch.setattr(youtube_addon, "get", lambda name: object() if name == "youtube-radio" else None)
    lines = []
    monkeypatch.setattr(radio_dj, "search", lambda ls: (lines.extend(ls), [
        {"id": "live0000002", "title": "Massive Attack - Teardrop (Live)", "channel": "Massive Attack", "dur": 330}])[1])
    res, obj = call(server, "GET", "/music/versions?uri=yt:https://www.youtube.com/watch?v=u7K72X4eo_s&title=Massive%20Attack%20-%20Teardrop", headers=AUTH)
    assert res.status == 200, obj
    assert obj["source"] == "youtube-radio" and lines[0] == "Massive Attack - Teardrop" and "Massive Attack - Teardrop live" in lines
    assert [(v["id"], v["kind"]) for v in obj["versions"]] == [("live0000002", "live")]


def test_a_versions_kind_leaves_the_songs_own_words_out():
    from agent_media_core.music_versions import kind_of
    assert kind_of("Oasis - Live Forever (Official Video)", "Oasis", "Oasis", "Live Forever") == "original"
    assert kind_of("Oasis - Live Forever (Live at Knebworth)", "Oasis", "Oasis", "Live Forever") == "live"
    assert kind_of("Wonderwall (Remastered 2014)", "Oasis", "Oasis", "Wonderwall") == "remaster"
    assert kind_of("Wonderwall", "Ryan Adams", "Oasis", "Wonderwall") == "cover"
